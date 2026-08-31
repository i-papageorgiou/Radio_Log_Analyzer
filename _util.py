"""Small shared helpers used across the API client modules."""

import difflib
import re


def normalize_key(s: str) -> str:
    """Normalize a string for use as an in-memory cache-dict key.

    Only ever used for cache lookups/storage — never for the actual strings
    sent to Spotify/Discogs/MusicBrainz search queries, so this never changes
    what gets searched for, only which rows end up sharing a cache entry
    (e.g. "Kendrick Lamar" and " kendrick lamar" hit the same cache slot).
    """
    return s.strip().casefold()


# Separators that show up in DJ-entered "Artist Name" fields when a track has
# multiple credited artists (e.g. "Grover Washington Jr. And Bill Withers",
# "Andrianne Lenker, Buck Meek"). Field-qualified searches (Spotify's
# `artist:...`) don't parse a whole multi-artist string, so querying with just
# the first-listed artist recovers an exact match with no fuzzy matching
# involved at all.
_MULTI_ARTIST_SEP_RE = re.compile(
    r"\s*(?:,|&|/|\bfeat\.?\b|\bft\.?\b|\band\b|\bx\b)\s*", re.IGNORECASE
)

# Trailing qualifiers DJs add by hand that don't appear in the canonical
# titles Spotify/Discogs index under (e.g. "Folded (remix)", "360 (clean)"),
# including a featured-artist clause repeated in the title itself (e.g.
# "Instant Crush (featuring Julian Casablancas)") — Spotify's own field-
# qualified search treats that whole parenthetical as part of the title to
# match exactly, so a DJ's "featuring"/"ft."/"feat." spelling that doesn't
# match the catalog's own wording breaks the strict query for no reason.
_TITLE_QUALIFIER_RE = re.compile(
    r"\s*[\(\[][^()\[\]]*\b(remix|clean|live|explicit|edit|version|acoustic|"
    r"radio|extended|instrumental|demo|mono|stereo|remaster\w*|"
    r"feat\.?|ft\.?|featuring)\b[^()\[\]]*[\)\]]\s*$",
    re.IGNORECASE,
)

# A leading zero-padded track number from a playout-system/filename export
# (e.g. "06 Wayne Rooney"). Deliberately requires a *leading zero* rather than
# any leading digits — genuine song titles that start with a number ("24
# Hours", "99 Problems", "1979", "7 Rings") are never zero-padded, so this
# only strips the auto-generated-track-listing pattern, not real title text.
_TRACK_NUM_PREFIX_RE = re.compile(r"^0\d{1,2}[\.\-_\s]+")

_PUNCT_RE = re.compile(r"[^a-z0-9\s]")
_WHITESPACE_RE = re.compile(r"\s+")


def primary_artist(artist_name: str) -> str:
    """Return just the first-listed artist from a possibly multi-artist string.

    Query-building only — the raw `artist_name` is still what gets written to
    output columns and used as the cache key. This never changes stored data,
    only which substring gets sent as the search query.
    """
    if not artist_name:
        return artist_name
    first = _MULTI_ARTIST_SEP_RE.split(artist_name.strip(), maxsplit=1)[0]
    return first.strip() or artist_name.strip()


def strip_title_qualifiers(title: str) -> str:
    """Strip a trailing "(remix)"/"(clean)"/"(live)"/"(feat. X)"/etc.
    qualifier and a leading zero-padded track number, for query-building
    (and match-scoring — see `_normalize_for_match`) only — never changes
    the Song Title that gets written out.
    """
    if not title:
        return title
    stripped = _TITLE_QUALIFIER_RE.sub("", title.strip())
    stripped = _TRACK_NUM_PREFIX_RE.sub("", stripped.strip())
    return stripped.strip() or title.strip()


def _normalize_for_match(s: str) -> str:
    # Apply the same qualifier/feat-clause stripping used for query-building
    # to *both* sides of a match_score() comparison. This matters most for
    # the candidate side: a Spotify catalog title often keeps its own
    # "(feat. X)" suffix (e.g. "Instant Crush (feat. Julian Casablancas)"),
    # so without stripping it here too, a query already cleaned down to just
    # "Instant Crush" would score artificially low against it.
    s = strip_title_qualifiers(s)
    s = s.lower()
    s = _PUNCT_RE.sub(" ", s)
    return _WHITESPACE_RE.sub(" ", s).strip()


def match_score(a: str, b: str) -> float:
    """Similarity ratio (0-1) between two strings, ignoring case/punctuation/
    whitespace differences. Used only to score fallback-search candidates —
    never for cache keys or output.
    """
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, _normalize_for_match(a), _normalize_for_match(b)).ratio()


def is_plausible_match(
    query_track: str,
    query_artist: str,
    candidate_track: str,
    candidate_artist: str,
    track_min: float = 0.55,
    artist_min: float = 0.55,
) -> bool:
    """Gate for accepting a broadened (non-field-qualified) fallback search
    result. Requires BOTH the track-name and artist-name similarity to clear
    their thresholds — a plain-text search always returns its best-effort top
    match even when there's no good one, so a track-only or artist-only check
    isn't enough (e.g. an exact-title cover by the wrong artist would pass a
    track-only gate but should be rejected).
    """
    return (
        match_score(query_track, candidate_track) >= track_min
        and match_score(query_artist, candidate_artist) >= artist_min
    )
