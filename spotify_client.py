"""Spotify lookups: album name, popularity, and release year for a track."""

import threading
import time

import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

from . import config
from ._util import is_plausible_match, normalize_key, primary_artist, strip_title_qualifiers


def get_client() -> spotipy.Spotify:
    return spotipy.Spotify(
        auth_manager=SpotifyClientCredentials(
            client_id=config.SPOTIFY_CLIENT_ID,
            client_secret=config.SPOTIFY_CLIENT_SECRET,
        )
    )


class SpotifyLookup:
    """Looks up track info with an in-memory cache and 429 retry handling.

    Safe to call concurrently from multiple threads: `spotipy.Spotify`
    maintains its own persistent `requests.Session()` internally (reused
    automatically since `self.sp` is built once here), the cache and
    per-minute throttle state are lock-protected, and an in-flight map
    ensures two threads racing on the same (track, artist) never both issue
    an outbound request.
    """

    def __init__(self, delay: float = 0.3, max_requests_per_minute: int = 120,
                 fuzzy_fallback: bool = True):
        self.sp = get_client()
        self.delay = delay
        self.max_requests_per_minute = max_requests_per_minute
        self.fuzzy_fallback = fuzzy_fallback
        self._request_count = 0
        self._minute_start = time.time()
        self._cache: dict[str, tuple] = {}
        self._cache_lock = threading.Lock()
        self._throttle_lock = threading.Lock()
        self._inflight: dict[str, threading.Event] = {}
        # Fallback-match bookkeeping, purely for end-of-run reporting.
        self.fallback_hits = 0
        self.fallback_misses = 0

    def _throttle(self) -> None:
        """Block until a request slot is free, without holding the lock
        across the sleep (so cache hits on other threads never wait on it).
        """
        while True:
            with self._throttle_lock:
                now = time.time()
                elapsed = now - self._minute_start
                if elapsed >= 60:
                    self._request_count = 0
                    self._minute_start = now
                    elapsed = 0
                if self._request_count < self.max_requests_per_minute:
                    self._request_count += 1
                    return
                wait = 60 - elapsed + 0.05
            print(f"\n  Spotify rate-limit guard: pausing {wait:.0f}s...")
            time.sleep(wait)

    @staticmethod
    def _extract_result(track: dict) -> tuple:
        album_name = track["album"]["name"]
        popularity = track.get("popularity")
        release_date = track["album"].get("release_date", "")
        release_year = int(release_date.split("-")[0]) if release_date else None
        return album_name, popularity, release_year

    def _search_and_gate(self, query: str, track_name: str, artist_name: str):
        """Run one search query and return the first candidate whose track
        AND artist both plausibly match, or None. Shared by every fallback
        query variant in `_fallback_search()` below.
        """
        results = self.sp.search(q=query, type="track", limit=5)
        for candidate in results.get("tracks", {}).get("items", []):
            cand_track = candidate.get("name", "")
            cand_artist = (candidate.get("artists") or [{}])[0].get("name", "")
            if is_plausible_match(track_name, artist_name, cand_track, cand_artist):
                return self._extract_result(candidate)
        return None

    def _fallback_search(self, track_name: str, artist_name: str) -> tuple:
        """Broadened, non-field-qualified search used only after the strict
        Tier 1 query finds nothing. Best-effort: any error here just means
        the row stays missing, same as if fallback weren't attempted — it
        never raises out to the caller.

        Tries, in order, until one passes the plausibility gate:
          1. combined "track artist" text (Tier 2) — works for most typos.
          2. track title alone (Tier 3) — recovers cases where the *artist*
             portion of the combined query is what throws off Spotify's own
             ranking (verified live: a typo'd/oddly-cased artist name can
             bury the correct track under same-titled tracks by other
             artists in the combined search, even though a track-only
             search puts it at rank 0). Still gated on artist similarity via
             `is_plausible_match`, so an unrelated track sharing the title
             is rejected exactly as before — this only widens which search
             *finds* a candidate, not what counts as a match.
          3. if the title contains "/" (a DJ medley/mashup entry mashing two
             song titles together), each "/"-separated half alone — same
             gate, just two more shots at a match.
        """
        try:
            candidate = self._search_and_gate(f"{track_name} {artist_name}", track_name, artist_name)
            if candidate is None:
                candidate = self._search_and_gate(track_name, track_name, artist_name)
            if candidate is None and "/" in track_name:
                for half in track_name.split("/"):
                    half = half.strip()
                    if not half:
                        continue
                    candidate = self._search_and_gate(half, half, artist_name)
                    if candidate is not None:
                        break
            if candidate is not None:
                with self._cache_lock:
                    self.fallback_hits += 1
                return candidate
        except Exception:  # noqa: BLE001 - fallback is best-effort, never fatal
            pass
        with self._cache_lock:
            self.fallback_misses += 1
        return None, None, None

    def get_track_info(self, track_name: str, artist_name: str, retries: int = 3):
        """Return (album_name, popularity, release_year) or (None, None, None)."""
        if not track_name or not artist_name:
            return None, None, None

        cache_key = f"{normalize_key(track_name)}||{normalize_key(artist_name)}"

        with self._cache_lock:
            if cache_key in self._cache:
                return self._cache[cache_key]
            event = self._inflight.get(cache_key)
            if event is None:
                event = threading.Event()
                self._inflight[cache_key] = event
                is_owner = True
            else:
                is_owner = False

        if not is_owner:
            # Another thread is already fetching this exact (track, artist);
            # wait for its result instead of issuing a duplicate request.
            event.wait()
            with self._cache_lock:
                return self._cache[cache_key]

        try:
            self._throttle()

            for attempt in range(retries):
                try:
                    query_track = strip_title_qualifiers(track_name)
                    query_artist = primary_artist(artist_name)
                    query = f"track:{query_track} artist:{query_artist}"
                    results = self.sp.search(q=query, type="track", limit=1)

                    if results.get("tracks", {}).get("items"):
                        result = self._extract_result(results["tracks"]["items"][0])
                    elif self.fuzzy_fallback:
                        # Tier 1 (field-qualified, exact-ish) found nothing —
                        # typos or a multi-artist string that survived
                        # primary_artist() can still cause this. Try a
                        # broadened plain-text search, but only accept a
                        # candidate that plausibly matches both fields, so a
                        # miss stays a miss rather than becoming a wrong hit.
                        result = self._fallback_search(query_track, query_artist)
                    else:
                        result = (None, None, None)

                    with self._cache_lock:
                        self._cache[cache_key] = result
                    time.sleep(self.delay)
                    return result

                except spotipy.exceptions.SpotifyException as e:
                    if getattr(e, "http_status", None) == 429:
                        retry_after = int(e.headers.get("Retry-After", 60)) if e.headers else 60
                        print(f"\n  Spotify rate limited. Waiting {retry_after + 10}s...")
                        time.sleep(retry_after + 10)
                        continue
                    if attempt < retries - 1:
                        time.sleep(2 ** attempt)
                        continue
                    print(f"Spotify error for '{track_name}' by '{artist_name}': {e}")
                    return None, None, None
                except Exception as e:  # noqa: BLE001 - log and move on
                    if attempt < retries - 1:
                        time.sleep(2)
                        continue
                    print(f"Unexpected error for '{track_name}' by '{artist_name}': {e}")
                    return None, None, None

            return None, None, None

        finally:
            with self._cache_lock:
                self._inflight.pop(cache_key, None)
            event.set()
