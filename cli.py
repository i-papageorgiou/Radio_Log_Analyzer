"""Command-line entry point.

Usage:
    radio-logs-analyzer --input logs.csv --output enriched_logs.xlsx
    radio-logs-analyzer --input logs.csv --output enriched_logs.xlsx --include-country
"""

import argparse
import sys

import pandas as pd

from . import config
from .pipeline import _DEFAULT_COLUMN_GUESSES, _guess_column, RadioLogsProcessor
from .reports import save_html_report


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Enrich a radio station song log with album, popularity, "
                    "genre, and (optionally) artist country data."
    )
    parser.add_argument("--input", "-i", required=True,
                        help="Path to the input CSV, or a Google Sheets CSV export URL.")
    parser.add_argument("--output", "-o", required=True,
                        help="Path to write the enriched output (.xlsx or .csv).")

    parser.add_argument("--report", default=None,
                        help="Path to write the HTML summary report (metrics, findings, "
                             "and charts). Defaults to --output with '_report.html' "
                             "appended (e.g. enriched_log.xlsx -> enriched_log_report.html).")
    parser.add_argument("--no-report", action="store_true",
                        help="Don't generate the HTML summary report, just the enriched output.")

    parser.add_argument("--track-col", default=None,
                        help="Column name for song title, if not auto-detected.")
    parser.add_argument("--artist-col", default=None,
                        help="Column name for artist name, if not auto-detected.")
    parser.add_argument("--date-col", default=None,
                        help="Column name for date, if not auto-detected.")
    parser.add_argument("--time-col", default=None,
                        help="Column name for time, if not auto-detected.")

    parser.add_argument("--no-forward-fill-date", action="store_true",
                        help="Don't carry the last known date down through blank rows "
                             "(useful if your log already has a date on every row).")

    parser.add_argument("--subgenres", type=int, default=3,
                        help="Number of subgenre columns to extract (default: 3).")
    parser.add_argument("--include-country", action="store_true",
                        help="Also look up each artist's country via MusicBrainz. "
                             "This is slow (~1.1s per unique artist) due to MusicBrainz's "
                             "rate limit.")

    parser.add_argument("--max-workers", type=int, default=3,
                        help="Number of tracks to look up concurrently during the "
                             "Spotify+Discogs stage (default: 3). Higher values don't "
                             "reliably speed things up further — Discogs' own rate "
                             "limit is the real ceiling, so extra workers past what's "
                             "needed to keep that pipeline saturated mostly add "
                             "contention (more self-throttle pauses, and at high "
                             "enough values, real 429s) rather than throughput. See "
                             "the Rate limits section in README.md before raising this.")
    parser.add_argument("--sequential", action="store_true",
                        help="Run Spotify then Discogs as two full sequential passes "
                             "instead of pipelining them concurrently per row (slower; "
                             "mainly useful for debugging or comparing against a "
                             "known-good baseline).")
    parser.add_argument("--no-fuzzy-fallback", action="store_true",
                        help="Disable the broadened fallback search that kicks in when "
                             "the strict Spotify/Discogs lookup finds nothing (usually "
                             "due to a misspelled artist/track name in the log). The "
                             "fallback only accepts a candidate whose name plausibly "
                             "matches the input, so it shouldn't introduce wrong matches "
                             "— pass this flag to fall back to today's strict-only "
                             "behavior if you want to compare or suspect it of a bad match.")

    parser.add_argument("--spotify-delay", type=float, default=0.3,
                        help="Seconds to sleep between Spotify requests (default: 0.3).")
    parser.add_argument("--spotify-max-per-minute", type=int, default=120,
                        help="Spotify request budget per minute before pausing (default: 120).")
    parser.add_argument("--discogs-delay", type=float, default=1.5,
                        help="Seconds to sleep between Discogs requests (default: 1.5).")
    parser.add_argument("--discogs-max-per-minute", type=int, default=45,
                        help="Discogs request budget per minute before pausing (default: 45, "
                             "Discogs' documented limit is 60/min for authenticated requests).")

    return parser


def load_input(path: str) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except Exception as e:  # noqa: BLE001
        print(f"Could not read input file '{path}': {e}", file=sys.stderr)
        sys.exit(1)


def _prompt_for_column(df: pd.DataFrame, field_label: str) -> str:
    """Ask the user to pick which column holds `field_label` (e.g. "song
    title" or "artist name") by number, once auto-detection and any
    --track-col/--artist-col override have both come up empty. Loops until
    a valid number is entered; exits with a pointer to the override flags
    if stdin isn't interactive (e.g. the CLI is run from a script/cron).
    """
    print(f"\nCould not automatically identify the '{field_label}' column.")
    print("Columns found in the file:")
    for i, col in enumerate(df.columns, start=1):
        print(f"  {i}. {col}")
    while True:
        try:
            raw = input(f"Enter the number of the column to use for {field_label}: ").strip()
        except EOFError:
            print(
                f"\nNo input available to resolve the '{field_label}' column "
                "(stdin isn't interactive). Re-run with --track-col/--artist-col "
                "to specify it directly.",
                file=sys.stderr,
            )
            sys.exit(1)
        if not raw.isdigit() or not (1 <= int(raw) <= len(df.columns)):
            print(f"  Please enter a number between 1 and {len(df.columns)}.")
            continue
        return df.columns[int(raw) - 1]


def resolve_required_columns(df: pd.DataFrame, track_col: str | None, artist_col: str | None) -> tuple:
    """Resolve the Song Title / Artist Name columns for `df`.

    Tries, in order: an explicit --track-col/--artist-col value, then
    auto-detection from common header names, then (only as a last resort)
    an interactive numbered prompt listing the file's actual columns —
    rather than failing outright the way a missing column used to.
    """
    resolved_track = track_col or _guess_column(df, _DEFAULT_COLUMN_GUESSES["track"])
    resolved_artist = artist_col or _guess_column(df, _DEFAULT_COLUMN_GUESSES["artist"])
    if not resolved_track:
        resolved_track = _prompt_for_column(df, "song title")
    if not resolved_artist:
        resolved_artist = _prompt_for_column(df, "artist name")
    return resolved_track, resolved_artist


def save_output(df: pd.DataFrame, path: str) -> None:
    if path.lower().endswith(".csv"):
        df.to_csv(path, index=False)
    else:
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        df.to_excel(path, index=False)
    print(f"\nSaved enriched log to: {path}")


def _default_report_path(output_path: str) -> str:
    """Derive a report path from --output, e.g. enriched_log.xlsx ->
    enriched_log_report.html, enriched_log.csv -> enriched_log_report.html.
    """
    base = output_path
    for ext in (".xlsx", ".csv"):
        if base.lower().endswith(ext):
            base = base[: -len(ext)]
            break
    return f"{base}_report.html"


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    config.check_credentials(require_discogs=True)

    df_raw = load_input(args.input)
    print(f"Loaded {len(df_raw)} rows from {args.input}")

    track_col, artist_col = resolve_required_columns(df_raw, args.track_col, args.artist_col)

    processor = RadioLogsProcessor(
        df_raw,
        date_col=args.date_col,
        time_col=args.time_col,
        track_col=track_col,
        artist_col=artist_col,
        spotify_delay=args.spotify_delay,
        spotify_max_per_minute=args.spotify_max_per_minute,
        discogs_delay=args.discogs_delay,
        discogs_max_per_minute=args.discogs_max_per_minute,
        fuzzy_fallback=not args.no_fuzzy_fallback,
    )

    result = processor.run(
        n_subgenres=args.subgenres,
        include_country=args.include_country,
        forward_fill_date=not args.no_forward_fill_date,
        max_workers=args.max_workers,
        sequential=args.sequential,
    )

    save_output(result, args.output)

    if not args.no_report:
        report_path = args.report or _default_report_path(args.output)
        save_html_report(result, report_path)


if __name__ == "__main__":
    main()
