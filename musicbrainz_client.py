"""MusicBrainz lookups: artist country of origin.

MusicBrainz enforces a strict 1 request/second limit and asks that every
request include an identifying User-Agent. Set MUSICBRAINZ_CONTACT (an
email or URL) in your .env so your requests aren't blocked.

Note: the artist *search* endpoint already returns each artist's
country/area as part of its core representation, so a single search
request is enough — no need for a second per-artist detail lookup. That
cuts MusicBrainz time in half (~1.1s/artist instead of ~2.2s/artist) for
the exact same underlying data.
"""

import time

import requests

from . import config
from ._util import normalize_key


class MusicBrainzLookup:
    """Looks up artist country with caching. Always sleeps >=1.1s/request."""

    def __init__(self):
        contact = f" ({config.MUSICBRAINZ_CONTACT})" if config.MUSICBRAINZ_CONTACT else ""
        self.headers = {"User-Agent": f"{config.MUSICBRAINZ_APP_NAME}{contact}"}
        self._cache: dict[str, str | None] = {}
        # Keyed by MusicBrainz area id — separate from the artist-name
        # cache above, so repeated areas (many artists from the same city)
        # only pay the extra hierarchy-walk lookup once.
        self._area_cache: dict[str, str | None] = {}

        self.session = requests.Session()
        self.session.headers.update(self.headers)

    def _get(self, url: str, params: dict, retries: int = 2) -> dict | None:
        """GET url, always sleeping >=1.1s/request regardless of outcome."""
        for attempt in range(retries):
            try:
                resp = self.session.get(url, params=params, timeout=10)
                time.sleep(1.1)

                if resp.status_code == 200:
                    return resp.json()

                if resp.status_code >= 500 and attempt < retries - 1:
                    # Transient server error — retry once rather than
                    # permanently treating this as "nothing found".
                    continue
                return None

            except Exception as e:  # noqa: BLE001
                if attempt < retries - 1:
                    continue
                print(f"Error fetching {url}: {e}")
                return None

        return None

    def _country_from_area(self, area: dict | None, max_hops: int = 3) -> str | None:
        """Resolve an artist's MusicBrainz `area` down to a country.

        Areas can be City- or Subdivision-typed (no reliable ISO code),
        in which case we have to walk the area hierarchy via `part of`
        relations until we hit a Country-typed ancestor, or give up after
        `max_hops` hops.
        """
        if not isinstance(area, dict):
            return None

        if area.get("type") == "Country":
            iso_codes = area.get("iso-3166-1-codes")
            return iso_codes[0] if iso_codes else area.get("name")

        area_id = area.get("id")
        if not area_id:
            return None

        if area_id in self._area_cache:
            return self._area_cache[area_id]

        result = self._walk_area_hierarchy(area_id, max_hops)
        self._area_cache[area_id] = result
        return result

    def _walk_area_hierarchy(self, area_id: str, hops_remaining: int) -> str | None:
        if hops_remaining <= 0:
            return None

        data = self._get(
            f"{config.MUSICBRAINZ_BASE_URL}/area/{area_id}",
            params={"inc": "area-rels", "fmt": "json"},
        )
        if not data:
            return None

        for rel in data.get("relations", []):
            if rel.get("type") != "part of" or rel.get("direction") != "backward":
                continue
            parent = rel.get("area")
            if not isinstance(parent, dict):
                continue

            if parent.get("type") == "Country":
                iso_codes = parent.get("iso-3166-1-codes")
                return iso_codes[0] if iso_codes else parent.get("name")

            parent_id = parent.get("id")
            if not parent_id:
                continue

            if parent_id in self._area_cache:
                return self._area_cache[parent_id]

            result = self._walk_area_hierarchy(parent_id, hops_remaining - 1)
            self._area_cache[parent_id] = result
            return result

        return None

    def get_country(self, artist_name: str, retries: int = 2):
        if not artist_name:
            return None

        cache_key = normalize_key(artist_name)
        if cache_key in self._cache:
            return self._cache[cache_key]

        country = None
        data = self._get(
            f"{config.MUSICBRAINZ_BASE_URL}/artist",
            params={"query": f'artist:"{artist_name}"', "fmt": "json", "limit": 1},
            retries=retries,
        )
        if data and data.get("artists"):
            artist = data["artists"][0]
            country = artist.get("country") or self._country_from_area(artist.get("area"))

        self._cache[cache_key] = country
        return country
