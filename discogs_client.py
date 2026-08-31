"""Discogs lookups: genre and style tags for an album."""

import threading
import time

import requests

from . import config
from ._util import is_plausible_match, normalize_key, primary_artist


class DiscogsLookup:
    """Looks up genres/styles for an (album, artist) pair with caching.

    Safe to call concurrently from multiple threads: requests go through a
    single shared `requests.Session()` (connection reuse), the cache and
    per-minute throttle state are lock-protected, and an in-flight map
    ensures two threads racing on the same (album, artist) never both issue
    an outbound request.
    """

    def __init__(self, delay: float = 1.5, max_requests_per_minute: int = 45,
                 fuzzy_fallback: bool = True):
        self.delay = delay
        self.max_requests_per_minute = max_requests_per_minute
        self.fuzzy_fallback = fuzzy_fallback
        self._request_count = 0
        self._minute_start = time.time()
        self._cache: dict[str, list | None] = {}
        self._cache_lock = threading.Lock()
        self._throttle_lock = threading.Lock()
        self._inflight: dict[str, threading.Event] = {}
        # Fallback-match bookkeeping, purely for end-of-run reporting.
        self.fallback_hits = 0
        self.fallback_misses = 0

        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Discogs token={config.DISCOGS_TOKEN}"})

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
            print(f"\n  Discogs rate-limit guard: pausing {wait:.0f}s...")
            time.sleep(wait)

    def _fallback_search(self, album_name: str, artist_name: str):
        """Broadened, album-only search used only after the artist+album
        Tier 1 query finds nothing. Best-effort: any error here just means
        the row stays missing, same as if fallback weren't attempted — it
        never raises out to the caller.

        Discogs' search `title` field comes back as "Artist - Release Title"
        (verified against the live API), so the candidate's artist is
        recovered by splitting on the first " - ".
        """
        try:
            response = self.session.get(
                f"{config.DISCOGS_BASE_URL}/database/search",
                params={"q": album_name, "type": "release", "per_page": 5},
                timeout=10,
            )
            if response.status_code != 200:
                with self._cache_lock:
                    self.fallback_misses += 1
                return None

            for candidate in response.json().get("results", []):
                title = candidate.get("title", "")
                cand_artist, _, cand_album = title.partition(" - ")
                if not cand_album:
                    cand_artist, cand_album = "", title
                if is_plausible_match(album_name, artist_name, cand_album, cand_artist):
                    combined = candidate.get("genre", []) + candidate.get("style", [])
                    with self._cache_lock:
                        self.fallback_hits += 1
                    return combined or None
        except Exception:  # noqa: BLE001 - fallback is best-effort, never fatal
            pass
        with self._cache_lock:
            self.fallback_misses += 1
        return None

    def get_genres(self, album_name: str, artist_name: str, retries: int = 3):
        """Return a list of genre/style tags, or None if nothing found."""
        if not album_name or not artist_name:
            return None

        cache_key = f"{normalize_key(album_name)}||{normalize_key(artist_name)}"

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
            # Another thread is already fetching this exact (album, artist);
            # wait for its result instead of issuing a duplicate request.
            event.wait()
            with self._cache_lock:
                return self._cache[cache_key]

        try:
            self._throttle()

            query_artist = primary_artist(artist_name)
            params = {"q": f"{query_artist} {album_name}", "type": "release", "per_page": 1}

            for attempt in range(retries):
                try:
                    response = self.session.get(
                        f"{config.DISCOGS_BASE_URL}/database/search",
                        params=params,
                        timeout=10,
                    )

                    if response.status_code == 429:
                        retry_after = int(response.headers.get("Retry-After", 60))
                        print(f"\n  Discogs rate limited (attempt {attempt+1}/{retries}). "
                              f"Waiting {retry_after + 10}s...")
                        time.sleep(retry_after + 10)
                        continue

                    if response.status_code == 200:
                        data = response.json()
                        if data.get("results"):
                            result = data["results"][0]
                            genres = result.get("genre", [])
                            styles = result.get("style", [])
                            combined = genres + styles
                            with self._cache_lock:
                                self._cache[cache_key] = combined or None
                            time.sleep(self.delay)
                            return self._cache[cache_key]
                        # Tier 1 (artist+album query) found nothing. Try a
                        # broadened album-only search before giving up, but
                        # only accept a candidate whose own listed artist
                        # plausibly matches ours — same reasoning as
                        # Spotify's fallback: a miss should stay a miss
                        # rather than become a wrong genre.
                        if self.fuzzy_fallback:
                            combined = self._fallback_search(album_name, query_artist)
                        else:
                            combined = None
                        with self._cache_lock:
                            self._cache[cache_key] = combined
                        time.sleep(self.delay)
                        return combined

                    if response.status_code >= 500:
                        # Transient server-side error — retry with backoff instead
                        # of permanently caching this album/artist as "not found".
                        print(f"\n  Discogs server error {response.status_code} for "
                              f"'{album_name}' (attempt {attempt+1}/{retries}).")
                        if attempt < retries - 1:
                            time.sleep(5)
                            continue
                        print(f"\n  Discogs still erroring after {retries} attempts; giving up.")
                        with self._cache_lock:
                            self._cache[cache_key] = None
                        return None

                    # Other non-retryable status codes (401, 403, 404, ...).
                    with self._cache_lock:
                        self._cache[cache_key] = None
                    time.sleep(self.delay)
                    return None

                except requests.exceptions.Timeout:
                    print(f"\n  Discogs timeout (attempt {attempt+1}/{retries})")
                    if attempt < retries - 1:
                        time.sleep(5)
                        continue
                except Exception as e:  # noqa: BLE001
                    print(f"\n  Discogs error for '{album_name}': {e}")
                    if attempt < retries - 1:
                        time.sleep(5)
                        continue
                    return None

            with self._cache_lock:
                self._cache[cache_key] = None
            return None

        finally:
            with self._cache_lock:
                self._inflight.pop(cache_key, None)
            event.set()
