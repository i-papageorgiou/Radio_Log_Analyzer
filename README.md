# Radio Logs Analyzer

Takes a raw radio station song log (the kind of Date / Time / Song Title /
Artist Name spreadsheet DJs fill out during their show) and returns an
enriched spreadsheet with:

- **Album name, Spotify popularity score, and release year** (via Spotify)
- **Genre and up to 3 subgenre tags**, normalized into consistent categories
  (via Discogs)
- **Artist country of origin** (optional, via MusicBrainz)

Built for college/community radio stations to turn raw logs into something
usable for reporting.

## Setup

1. **Clone and install**

   ```bash
   git clone https://github.com/i-papageorgiou/Radio_Log_Report.git
   cd Radio_Log_Report
   pip install -e .
   ```

2. **Get API credentials**

   - Spotify: create an app at the
     [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
     to get a Client ID and Client Secret.
   - Discogs: generate a personal access token at
     [Discogs Developer Settings](https://www.discogs.com/settings/developers).

3. **Configure credentials**

   ```bash
   cp .env.example .env
   ```

   Then edit `.env` and fill in your `SPOTIFY_CLIENT_ID`,
   `SPOTIFY_CLIENT_SECRET`, and `DISCOGS_TOKEN`. `.env` is git-ignored, so
   your credentials never get committed.

   If you plan to use `--include-country`, also set `MUSICBRAINZ_CONTACT`
   to your email or a project URL — MusicBrainz asks every request to
   identify a contact and may block ones that don't.

## Usage

### Command line

```bash
radio-logs-analyzer --input path/to/your_log.csv --output enriched_log.xlsx
```

Include artist country lookups (slower — MusicBrainz allows ~1 request/second):

```bash
radio-logs-analyzer --input log.csv --output enriched_log.xlsx --include-country
```

If your spreadsheet uses non-standard column headers, tell the script which
columns to use (it auto-detects common names like "Song Title" and "Artist
Name" otherwise):

```bash
radio-logs-analyzer --input log.csv --output enriched_log.xlsx \
  --track-col "Track" --artist-col "Performer" --date-col "Air Date"
```

If you skip these flags and auto-detection can't confidently identify the
song title and/or artist name column on its own, the script lists the
columns it found in your file, numbered, and asks you to type the number
for each one it couldn't guess — no need to know the flag names or edit the
file:

```
Could not automatically identify the 'artist name' column.
Columns found in the file:
  1. Air Date
  2. Performer
  3. Track
Enter the number of the column to use for artist name: 2
```

This only runs when needed (interactively, from a terminal); a
non-interactive run (e.g. from a script or cron job) should pass
`--track-col`/`--artist-col` explicitly instead, since there's no one there
to answer the prompt.

By default, Spotify and Discogs lookups are pipelined per track (concurrent,
3 workers) instead of run as two full sequential passes — same results,
noticeably faster (roughly 45% less wall-clock time on a 299-row test batch).
Tune concurrency with `--max-workers`, or fall back to the old strictly
sequential behavior with `--sequential` (useful for debugging):

```bash
radio-logs-analyzer --input log.csv --output enriched_log.xlsx --max-workers 6
radio-logs-analyzer --input log.csv --output enriched_log.xlsx --sequential
```

Note that raising `--max-workers` doesn't reliably buy more speed: testing
showed 3 and 6 workers finish in essentially the same time (Discogs' own
rate limit is the real ceiling on throughput, not worker count), while
pushing higher (10) measurably backfired — see the Rate limits section below.

Alongside the enriched output, every run also writes an HTML summary report
by default (e.g. `enriched_log.xlsx` → `enriched_log_report.html`) — see the
Report section below. Pass `--report other_name.html` to change where it's
written, or `--no-report` to skip it entirely:

```bash
radio-logs-analyzer --input log.csv --output enriched_log.xlsx --report show_summary.html
radio-logs-analyzer --input log.csv --output enriched_log.xlsx --no-report
```

Run `radio-logs-analyzer --help` for all options, including request-rate
controls for Spotify/Discogs.

### From a Google Sheet

Export your Google Sheet as CSV first (`File > Download > Comma Separated
Values`), or use the direct CSV export URL as `--input`:

```
https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/export?format=csv&gid=YOUR_GID
```

### As a Python library

```python
import pandas as pd
from radio_logs_analyzer.pipeline import RadioLogsProcessor
from radio_logs_analyzer.reports import print_summary

df_raw = pd.read_csv("your_log.csv")
processor = RadioLogsProcessor(df_raw)
enriched = processor.run(include_country=True)

enriched.to_excel("enriched_log.xlsx", index=False)
print_summary(enriched)
```

See `examples/sample_usage.py` for a runnable version.

## Expected input format

At minimum, your log needs a **song title** column and an **artist name**
column. Date and time columns are picked up automatically if present. A
typical Google Forms export looks like:

| Date | Time | Song Title | Artist Name | DJ Name - Show Name |
|------|------|------------|--------------|----------------------|
| 9/3/25 | 11:12 PM | The Birds Don't Sing | Clipse | Elijah Suk - WBlock |
| | 11:25 PM | Father | Jim Legxacy | |

Blank dates on later rows are automatically filled forward from the last
known date (disable with `--no-forward-fill-date`).

## Output columns

| Column | Source |
|---|---|
| Date, Time, Song Title, Artist Name | your original log |
| Album Name | Spotify |
| Popularity (0-100) | Spotify |
| Release Year | Spotify |
| Genre | Discogs, normalized to one of 15 top-level categories |
| Subgenre_1 / 2 / 3 | Discogs' raw style/genre tags, left as-is (not bucketed) |
| Country | MusicBrainz (only with `--include-country`) |

## Report

Every run also produces a self-contained HTML report (open it in any
browser — no server needed) next to the enriched output — a magazine-style
dashboard meant to be the shareable "here's what this batch of logs looked
like" document:

- **Masthead & KPI strip**: total plays, unique songs/artists/albums, and
  countries repped, plus the session date range and average popularity.
- **Most-played cards**: the most-played artist, track, and (if Album Name
  data exists) album, each with its play count.
- **Tracks over time**: a monthly bar chart, built only from rows with a
  fully dated entry (day/month-only rows with no year aren't counted), with
  the busiest and quietest months called out.
- **Popularity**: a distribution histogram (0–100 Spotify score), with the
  single most-common score bucket highlighted rather than always the
  bottom one — so a station that skews mainstream or underground both get
  an accurate highlight.
- **What's in rotation**: a genre pie chart, a subgenre pie chart for
  whichever genre turned out most common (only shown when that genre
  actually breaks down into more than one distinct subgenre — capped at 8
  slices, top 7 + an "Other" bucket, so a long tail of one-off tags doesn't
  turn it into an unreadable wheel of slivers), and a top-artists bar chart.
  When the dominant genre is Rock, raw Discogs tags are also consolidated
  with the same bucketing `genre_categorization.py` uses elsewhere
  (display-only — the Subgenre_1/2/3 output columns are untouched). Tags
  that just restate a fragment of a compound genre's own name (e.g.
  "Country" or "Folk" showing up under "Folk, World, & Country") are
  dropped rather than charted as if they were distinct subgenres.
- **Release years**: a decade bar chart plus the oldest track on record and
  the single release year with the most tracks logged.
- **Around the world** (only with `--include-country`): a D3 choropleth of
  artist countries of origin (zoomed to the populated world, not stretched
  out to fit an always-empty Antarctica), and a ranked list of the top
  countries outside whichever one dominates the log.

Every section is independently gated on its source column actually having
usable data (e.g. no Popularity values, or `--include-country` wasn't
used), so a log missing a field just gets fewer sections rather than one
shown broken or empty — same principle the enrichment pipeline itself
follows. All of the report's copy (which genre leads, which month peaked,
which country is excluded from the "rest of the world" ranking, etc.) is
computed from that run's actual data, not fixed wording.

Charts are interactive (Chart.js) and the world map is rendered with D3;
both load their JS from a CDN, along with the report's fonts, so viewing
the report needs an internet connection the first time it's opened in a
given browser session.

## Handling misspelled artist/track names

DJ-entered logs inevitably have typos, multi-artist fields (`"Grover Washington
Jr. And Bill Withers"`), and hand-added qualifiers (`"Fat Lip (clean Version)"`)
that a strict Spotify/Discogs search won't match. To reduce that data loss
without introducing wrong matches, each lookup now runs in two tiers:

1. **Tier 1** — the same close-to-exact search as before, but built from a
   cleaned query (first-listed artist only, trailing qualifiers — including
   a `"(feat. X)"`/`"(ft. X)"`/`"(featuring X)"` clause and a leading
   zero-padded track number like `"06 "` — stripped). This alone fixes
   multi-artist, qualifier-suffix, and featured-artist-in-title cases with
   zero fuzzy matching involved.
2. **Tier 2** (only if Tier 1 finds nothing) — a broadened, plain-text search
   (track + artist together) whose top candidates are only accepted if the
   candidate's own track/album name *and* artist name both plausibly match
   the input (normalized string-similarity gate). A miss stays a miss rather
   than risk becoming a wrong hit — the gate rejected every garbage candidate
   tested during development, including a same-title-wrong-artist cover.
3. **Tier 3, Spotify only** (only if Tier 2 also finds nothing) — retries
   with just the track title alone, still behind the same gate. This
   recovers cases where a typo'd or oddly-cased artist string throws off
   Spotify's own ranking for the *combined* query enough that the right
   track never shows up in it, even though a track-only search finds it
   immediately. For a `"Title A/Title B"` DJ medley/mashup entry, each half
   also gets a shot through this same tier.

At the end of a run, the console prints how many rows each tier's fallback
recovered vs. left missing. Pass `--no-fuzzy-fallback` to disable Tiers 2 and
3 and reproduce the old strict-only behavior (e.g. to compare, or if you
suspect a bad match). See `tests/test_log.md` (Tests 5-6) for the
verification runs and threshold-tuning notes. Some data loss remains
unavoidable this way — most notably non-Latin/transliterated titles, where
no amount of edit-distance matching substitutes for a real transliteration
scheme, and DJ-logged tracks/artists that simply aren't in Spotify's or
Discogs' catalog at all.

## Rate limits

- **Spotify**: generous, but the script throttles itself to stay well under
  limits and retries automatically on 429 responses.
- **Discogs**: ~60 requests/minute for authenticated requests; the script
  defaults to 45/minute, pauses automatically if it gets close, and retries
  automatically on transient server errors (5xx) and 429s. Since Spotify and
  Discogs lookups run concurrently by default, requests can arrive in small
  bursts rather than evenly spaced — if you see frequent rate-limit pauses
  in the output, try a lower `--max-workers`.
- **MusicBrainz**: hard limit of ~1 request/second, enforced with sleeps.
  One request per artist is enough (country/area data comes back with the
  search result itself), so this is why `--include-country` is opt-in — a
  log of a few hundred unique artists still takes a few minutes.

Large logs (thousands of tracks) can take a while to process because of
these limits. Results are cached in-memory during a run, so repeated
tracks/albums/artists in the same log only get looked up once.

**On tuning `--max-workers`:** a controlled experiment on a ~2000-track batch
(`--max-workers` 3 vs. 6 vs. 10, everything else held constant) found that
raising it doesn't reliably buy more speed — Discogs' own rate limit is the
real ceiling on throughput, not worker count. 3 and 6 workers finished in
essentially the same time; 3 just did it with about half the self-throttle
pauses and no real Discogs 429s, so **3 is the default**. Pushing to 10 was
a clear regression: real 429 responses appeared (never seen at 3 or 6, on
any tested batch size) and total time got *worse*, not just diminishing —
more contention outweighed the extra concurrency. Raising `--max-workers`
above the default isn't recommended without testing it against your own
data first; if you do, watch the console for `rate-limit guard` / `rate
limited` messages — frequent ones mean you've pushed too far. `--sequential`
remains the safest fallback (never triggers either kind of rate-limit pause)
at the cost of the concurrency speedup. Full experiment writeup in
`tests/test_log.md` (Test 4).

## Roadmap

- [x] Reporting layer with charts and a full dashboard (genre/subgenre
      breakdown, popularity distribution, plays-over-time trend, and an
      artist-country choropleth) — see the Report section above and
      `radio_logs_analyzer/reports.py`.
- [ ] Plays by DJ/show — the report currently has no notion of who was on
      air; would need a DJ/show column in the input to break stats down
      that way.
- [ ] Cross-run caching (SQLite) so re-processing a log doesn't re-query
      APIs for tracks you've already looked up.

## License

MIT
