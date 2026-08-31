"""End-to-end pipeline: raw song log -> enriched, analysis-ready dataframe."""

from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
from tqdm import tqdm

from .discogs_client import DiscogsLookup
from .genre_categorization import fix_genres
from .musicbrainz_client import MusicBrainzLookup
from .spotify_client import SpotifyLookup

# Common raw column names seen in Google Forms-style radio logs. The loader
# tries these (case-insensitive, substring match) before falling back to
# whatever the caller specifies explicitly.
_DEFAULT_COLUMN_GUESSES = {
    "date": ["date"],
    "time": ["time"],
    "track": ["song title", "track", "title"],
    "artist": ["artist name", "artist"],
}


def _guess_column(df: pd.DataFrame, candidates: list) -> str | None:
    lower_cols = {c.lower(): c for c in df.columns}
    for candidate in candidates:
        for lower_name, original_name in lower_cols.items():
            if candidate in lower_name:
                return original_name
    return None


class RadioLogsProcessor:
    """Cleans a raw song log and enriches it with Spotify/Discogs/MusicBrainz data."""

    def __init__(
        self,
        df_raw: pd.DataFrame,
        date_col: str | None = None,
        time_col: str | None = None,
        track_col: str | None = None,
        artist_col: str | None = None,
        spotify_delay: float = 0.3,
        spotify_max_per_minute: int = 120,
        discogs_delay: float = 1.5,
        discogs_max_per_minute: int = 45,
        fuzzy_fallback: bool = True,
    ):
        self.df_raw = df_raw.copy()

        self.date_col_in = date_col or _guess_column(df_raw, _DEFAULT_COLUMN_GUESSES["date"])
        self.time_col_in = time_col or _guess_column(df_raw, _DEFAULT_COLUMN_GUESSES["time"])
        self.track_col_in = track_col or _guess_column(df_raw, _DEFAULT_COLUMN_GUESSES["track"])
        self.artist_col_in = artist_col or _guess_column(df_raw, _DEFAULT_COLUMN_GUESSES["artist"])

        if not self.track_col_in or not self.artist_col_in:
            raise ValueError(
                "Could not identify Song Title / Artist Name columns automatically. "
                "Pass track_col= and artist_col= explicitly (see --track-col / --artist-col "
                "on the CLI). Columns found in file: " + ", ".join(df_raw.columns)
            )

        self.track_col = "Song Title"
        self.artist_col = "Artist Name"
        self.date_col = "Date"
        self.time_col = "Time"
        self.album_col = "Album Name"
        self.genre_col = "Genre"

        self.spotify = SpotifyLookup(delay=spotify_delay, max_requests_per_minute=spotify_max_per_minute,
                                      fuzzy_fallback=fuzzy_fallback)
        self.discogs = DiscogsLookup(delay=discogs_delay, max_requests_per_minute=discogs_max_per_minute,
                                      fuzzy_fallback=fuzzy_fallback)
        self.musicbrainz = MusicBrainzLookup()

    # ------------------------------------------------------------------
    # Step 1: cleaning
    # ------------------------------------------------------------------
    def preprocess(self, forward_fill_date: bool = True) -> pd.DataFrame:
        df = self.df_raw.copy()

        rename_map = {self.track_col_in: self.track_col, self.artist_col_in: self.artist_col}
        if self.date_col_in:
            rename_map[self.date_col_in] = self.date_col
        if self.time_col_in:
            rename_map[self.time_col_in] = self.time_col
        df = df.rename(columns=rename_map)

        df = df[df[self.track_col].notna()].copy()
        df = df[df[self.artist_col].notna()].copy()
        df = df.reset_index(drop=True)

        # Trim whitespace only — don't re-capitalize titles/artist names.
        # Many artists use intentional stylized casing (MF DOOM, AC/DC,
        # will.i.am, DJ Khaled) that title-casing would otherwise destroy.
        df[self.track_col] = df[self.track_col].astype(str).str.strip()
        df[self.artist_col] = df[self.artist_col].astype(str).str.strip()

        if forward_fill_date and self.date_col in df.columns:
            df[self.date_col] = df[self.date_col].ffill()

        self.df_clean = df
        return df

    # ------------------------------------------------------------------
    # Step 2: Spotify enrichment (album, popularity, release year)
    # ------------------------------------------------------------------
    def add_spotify_data(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        for col in (self.album_col, "Popularity", "Release Year"):
            if col not in df.columns:
                df[col] = None

        print(f"Looking up albums, popularity, and release years for {len(df)} tracks (Spotify)...")
        for idx in tqdm(df.index, desc="Spotify"):
            track = df.loc[idx, self.track_col]
            artist = df.loc[idx, self.artist_col]
            album, popularity, year = self.spotify.get_track_info(track, artist)
            df.loc[idx, self.album_col] = album
            df.loc[idx, "Popularity"] = popularity
            df.loc[idx, "Release Year"] = year

        self.df_spotify = df
        return df

    # ------------------------------------------------------------------
    # Step 3: Discogs enrichment (genre / subgenres)
    # ------------------------------------------------------------------
    def add_genre_data(self, df: pd.DataFrame, n_subgenres: int = 3) -> pd.DataFrame:
        df = df.copy()
        df[self.genre_col] = None

        print(f"Looking up genres for {len(df)} tracks (Discogs)...")
        for idx in tqdm(df.index, desc="Discogs"):
            album = df.loc[idx, self.album_col]
            artist = df.loc[idx, self.artist_col]
            if pd.isna(album) or pd.isna(artist):
                continue
            genres = self.discogs.get_genres(album, artist)
            df.loc[idx, self.genre_col] = ", ".join(genres) if genres else None

        df = self._split_genres(df, n_subgenres=n_subgenres)
        df = fix_genres(df, genre_col=self.genre_col,
                         subgenre_cols=tuple(f"Subgenre_{i+1}" for i in range(n_subgenres)))
        # Note: subgenres are intentionally left as Discogs' raw style/genre
        # tags (no bucketing) — only the top-level Genre column is normalized.

        self.df_genres = df
        return df

    def _split_genres(self, df: pd.DataFrame, n_subgenres: int = 3) -> pd.DataFrame:
        df = df.copy()
        for i in range(n_subgenres):
            df[f"Subgenre_{i+1}"] = None

        for idx in df.index:
            genre_value = df.loc[idx, self.genre_col]
            if pd.isna(genre_value) or genre_value == "Folk, World, & Country":
                continue
            parts = genre_value.split(", ")
            df.loc[idx, self.genre_col] = parts[0]
            for i in range(n_subgenres):
                df.loc[idx, f"Subgenre_{i+1}"] = parts[i + 1] if i + 1 < len(parts) else None

        return df

    # ------------------------------------------------------------------
    # Step 2+3 combined: Spotify and Discogs pipelined per row
    # ------------------------------------------------------------------
    def _add_spotify_and_genre_data_concurrent(
        self, df: pd.DataFrame, n_subgenres: int = 3, max_workers: int = 3
    ) -> pd.DataFrame:
        """Same result as add_spotify_data() + add_genre_data() run back to
        back, but each row's Spotify lookup and Discogs lookup (which needs
        that row's Spotify album result) run as one per-row task, and many
        rows' tasks run concurrently — so Discogs work for one row overlaps
        Spotify work for another instead of waiting for every row's Spotify
        lookup to finish first. Worker threads never touch the DataFrame;
        all `.loc` writes happen on the main thread as results come back.
        """
        df = df.copy()
        for col in (self.album_col, "Popularity", "Release Year"):
            if col not in df.columns:
                df[col] = None
        df[self.genre_col] = None

        rows = [(idx, df.loc[idx, self.track_col], df.loc[idx, self.artist_col])
                for idx in df.index]

        def process_row(idx, track, artist):
            album, popularity, year = self.spotify.get_track_info(track, artist)
            genres = None
            if pd.notna(album) and pd.notna(artist):
                genres = self.discogs.get_genres(album, artist)
            return idx, album, popularity, year, genres

        print(f"Looking up albums, popularity, release years, and genres for "
              f"{len(df)} tracks (Spotify+Discogs pipelined, {max_workers} workers)...")
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(process_row, idx, track, artist)
                       for idx, track, artist in rows]
            for fut in tqdm(as_completed(futures), total=len(futures), desc="Spotify+Discogs"):
                idx, album, popularity, year, genres = fut.result()
                df.loc[idx, self.album_col] = album
                df.loc[idx, "Popularity"] = popularity
                df.loc[idx, "Release Year"] = year
                df.loc[idx, self.genre_col] = ", ".join(genres) if genres else None

        df = self._split_genres(df, n_subgenres=n_subgenres)
        df = fix_genres(df, genre_col=self.genre_col,
                         subgenre_cols=tuple(f"Subgenre_{i+1}" for i in range(n_subgenres)))

        self.df_spotify = df
        self.df_genres = df
        return df

    # ------------------------------------------------------------------
    # Step 4 (optional, slow): MusicBrainz artist country
    # ------------------------------------------------------------------
    def add_country_data(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["Country"] = None

        print(f"Looking up artist countries for {len(df)} tracks (MusicBrainz, ~1.1s/request)...")
        for idx in tqdm(df.index, desc="MusicBrainz"):
            artist = df.loc[idx, self.artist_col]
            df.loc[idx, "Country"] = self.musicbrainz.get_country(artist)

        self.df_country = df
        return df

    # ------------------------------------------------------------------
    # Full pipeline
    # ------------------------------------------------------------------
    def run(self, n_subgenres: int = 3, include_country: bool = False,
            forward_fill_date: bool = True, max_workers: int = 3,
            sequential: bool = False) -> pd.DataFrame:
        """Run the full pipeline.

        By default, Spotify and Discogs lookups are pipelined per row
        (concurrent, `max_workers` workers) instead of run as two full
        sequential passes — same data, same matches, just faster. Pass
        `sequential=True` to reproduce the old strictly-sequential behavior
        (e.g. for debugging or comparing against a known-good baseline).
        """
        df = self.preprocess(forward_fill_date=forward_fill_date)
        if sequential:
            df = self.add_spotify_data(df)
            df = self.add_genre_data(df, n_subgenres=n_subgenres)
        else:
            df = self._add_spotify_and_genre_data_concurrent(
                df, n_subgenres=n_subgenres, max_workers=max_workers)
        self._print_fallback_summary()
        if include_country:
            df = self.add_country_data(df)
        return df

    def _print_fallback_summary(self) -> None:
        """Report how many rows the broadened fallback search (see
        SpotifyLookup/DiscogsLookup) recovered vs. still left missing, so the
        effect is visible without adding a column to the output.
        """
        sp_hits, sp_misses = self.spotify.fallback_hits, self.spotify.fallback_misses
        dc_hits, dc_misses = self.discogs.fallback_hits, self.discogs.fallback_misses
        if sp_hits + sp_misses:
            print(f"Spotify fallback search: recovered {sp_hits}, "
                  f"still missing {sp_misses} (of {sp_hits + sp_misses} rows Tier 1 missed).")
        if dc_hits + dc_misses:
            print(f"Discogs fallback search: recovered {dc_hits}, "
                  f"still missing {dc_misses} (of {dc_hits + dc_misses} rows Tier 1 missed).")
