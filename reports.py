"""Summary metrics, narrative findings, and a self-contained HTML report over
an enriched log.

`print_summary()` is the quick console version. `save_html_report()` /
`build_html_report()` produce a shareable, magazine-style dashboard —
masthead, KPI strip, most-played cards, monthly/popularity/genre/decade
charts, and (when `--include-country` was used) a world map — meant to sit
alongside the enriched CSV/XLSX as the other half of a run's output. Every
section is independently gated on its source column actually having usable
data, so a log missing e.g. Popularity or Country just gets fewer sections
rather than a broken or empty one. All copy is computed from whatever the
data actually shows on a given run (no fixed wording tied to any one
station's dataset shape).
"""

import html
import json
import re
from datetime import datetime
from importlib import resources

import pandas as pd

from .genre_categorization import categorize_rock_subgenre

# Max pie slices shown for genre / subgenre breakdowns (top groups + one
# "Other" bucket for the rest) — keeps a long tail of one-off tags from
# turning the chart into an unreadable wheel of slivers. Bucketing itself
# happens client-side (see bucketOther() in the rendering script) so the
# same helper handles both the genre pie and the subgenre pie.
_GENRE_OTHER_THRESHOLD = 0.01
_SUBGENRE_OTHER_THRESHOLD = 0.02

_WORD_RE = re.compile(r"[a-z0-9]+")


def _genre_name_words(genre: str) -> set:
    return set(_WORD_RE.findall(genre.lower()))


def _format_tier(labels: list, count: int) -> str:
    """Format a "most common X" result as a display string. A single winner
    is just "2025 (337)"; a tie is called out explicitly rather than
    silently picking one — named outright if only 2 things are tied, or the
    first 2 plus "and others" if there are more than that (so a big tie
    doesn't blow out the display).
    """
    if len(labels) == 1:
        return f"{labels[0]} ({count})"
    if len(labels) == 2:
        return f"{labels[0]} & {labels[1]} (tied, {count} each)"
    return f"{labels[0]}, {labels[1]}, and others (tied, {count} each)"


def _is_genre_name_restatement(tag, genre_words: set) -> bool:
    """True if `tag`'s words don't add anything beyond the dominant genre's
    own name. Some of Discogs' 15 top-level genres are themselves compound
    names ("Folk, World, & Country", "Funk / Soul", "Stage & Screen", "Brass
    & Military") — a track's raw Discogs genre/style can end up filing one of
    those name's own component words (e.g. "Country", "Folk", or "World"
    alone) as a Subgenre when it just got merged into the compound bucket.
    Charting that as if it were a distinct subgenre would just be
    re-splitting the bucket back into (parts of) its own name, so it's
    filtered out here rather than shown as a slice.
    """
    if pd.isna(tag):
        return False
    tag_words = set(_WORD_RE.findall(str(tag).lower()))
    return bool(tag_words) and tag_words <= genre_words


def summary_stats(df: pd.DataFrame) -> dict:
    stats = {
        "total_plays": len(df),
        "unique_tracks": df[["Song Title", "Artist Name"]].drop_duplicates().shape[0],
        "unique_artists": df["Artist Name"].nunique(),
    }

    if "Album Name" in df.columns:
        stats["unique_albums"] = df["Album Name"].nunique()

    if "Genre" in df.columns:
        stats["top_genres"] = df["Genre"].value_counts().head(10).to_dict()

    if "Popularity" in df.columns:
        pop = pd.to_numeric(df["Popularity"], errors="coerce")
        stats["avg_popularity"] = round(pop.mean(), 1) if pop.notna().any() else None

    if "Release Year" in df.columns:
        year = pd.to_numeric(df["Release Year"], errors="coerce")
        # Most common year and decade rather than a mean: an average year is
        # a number that may not correspond to any actual release. Ties are
        # called out explicitly (see _format_tier) instead of arbitrarily
        # picking one year/decade when several are equally common.
        if year.notna().any():
            yr = year.dropna().astype(int)

            year_counts = yr.value_counts()
            top_count = year_counts.max()
            top_years = sorted(year_counts[year_counts == top_count].index.tolist(), reverse=True)
            stats["top_release_year_display"] = _format_tier([str(y) for y in top_years], top_count)

            decade = (yr // 10) * 10
            decade_counts = decade.value_counts()
            top_dcount = decade_counts.max()
            top_decades = sorted(decade_counts[decade_counts == top_dcount].index.tolist(), reverse=True)
            stats["top_release_decade_display"] = _format_tier([f"{d}s" for d in top_decades], top_dcount)
        else:
            stats["top_release_year_display"] = None
            stats["top_release_decade_display"] = None

    if "Country" in df.columns:
        stats["top_countries"] = df["Country"].value_counts().head(10).to_dict()

    top_artists = df["Artist Name"].value_counts().head(10)
    stats["top_artists"] = top_artists.to_dict()

    return stats


def print_summary(df: pd.DataFrame) -> None:
    stats = summary_stats(df)

    print("\n=== Radio Log Summary ===")
    print(f"Total plays: {stats['total_plays']}")
    print(f"Unique tracks: {stats['unique_tracks']}")
    print(f"Unique artists: {stats['unique_artists']}")
    if stats.get("unique_albums") is not None:
        print(f"Unique albums: {stats['unique_albums']}")

    if stats.get("avg_popularity") is not None:
        print(f"Average Spotify popularity: {stats['avg_popularity']}")
    if stats.get("top_release_year_display") is not None:
        print(f"Most common release year: {stats['top_release_year_display']}")
    if stats.get("top_release_decade_display") is not None:
        print(f"Most common release decade: {stats['top_release_decade_display']}")

    if "top_genres" in stats:
        print("\nTop genres:")
        for genre, count in stats["top_genres"].items():
            print(f"  {genre}: {count}")

    print("\nTop artists:")
    for artist, count in stats["top_artists"].items():
        print(f"  {artist}: {count}")

    if "top_countries" in stats:
        print("\nTop artist countries:")
        for country, count in stats["top_countries"].items():
            print(f"  {country}: {count}")


# ----------------------------------------------------------------------
# World boundaries asset (bundled, ~180 countries, id = ISO alpha-3,
# properties.alpha2/properties.name used to match against the Country
# column's ISO alpha-2 values). Loaded once per process.
# ----------------------------------------------------------------------
_world_geometry_cache = None


def _load_world_geometry() -> dict:
    global _world_geometry_cache
    if _world_geometry_cache is None:
        asset = resources.files(__package__) / "data" / "world_countries.geojson"
        with resources.as_file(asset) as path, open(path, encoding="utf-8") as f:
            _world_geometry_cache = json.load(f)
    return _world_geometry_cache


def _fmt_date(ts) -> str:
    return f"{ts.strftime('%b')} {ts.day}, {ts.year}"


def _month_label(period: str, full: bool = False) -> str:
    year, month = period.split("-")
    fmt = "%B" if full else "%b"
    return datetime(int(year), int(month), 1).strftime(fmt) + " '" + year[2:]


# ----------------------------------------------------------------------
# Dashboard data — every stat the HTML report needs, split into `text`
# (rendered directly into the page server-side) and `chart` (the smaller
# JSON blob the client-side Chart.js/D3 script needs to draw charts and
# the world map). Each block below is independently guarded on its source
# column having usable data, and simply contributes nothing if not.
# ----------------------------------------------------------------------
def _dashboard_data(df: pd.DataFrame) -> dict:
    t: dict = {"total_plays": len(df)}
    chart: dict = {"total_plays": len(df)}
    geo = None

    t["unique_songs"] = df[["Song Title", "Artist Name"]].drop_duplicates().shape[0]

    artist_counts = df["Artist Name"].value_counts()
    t["unique_artists"] = int(len(artist_counts))
    if not artist_counts.empty:
        t["most_played_artist"] = {"name": str(artist_counts.index[0]), "count": int(artist_counts.iloc[0])}
    one_off = int((artist_counts == 1).sum())
    t["one_off_pct"] = round(100 * one_off / len(artist_counts), 1) if len(artist_counts) else 0.0
    t["one_off_total"] = int(len(artist_counts))

    song_counts = df[["Song Title", "Artist Name"]].value_counts()
    if not song_counts.empty:
        (title, artist), count = song_counts.index[0], song_counts.iloc[0]
        t["most_played_song"] = {"title": str(title), "artist": str(artist), "count": int(count)}

    has_album = "Album Name" in df.columns and df["Album Name"].notna().any()
    if has_album:
        t["unique_albums"] = int(df["Album Name"].nunique())
        album_rows = df.dropna(subset=["Album Name"])
        album_counts = album_rows.groupby(["Album Name", "Artist Name"]).size().sort_values(ascending=False)
        if not album_counts.empty:
            (album, artist), count = album_counts.index[0], album_counts.iloc[0]
            t["most_played_album"] = {"title": str(album), "artist": str(artist), "count": int(count)}

    has_popularity = "Popularity" in df.columns
    pop = pd.to_numeric(df["Popularity"], errors="coerce") if has_popularity else None
    has_popularity = bool(has_popularity and pop.notna().any())
    if has_popularity:
        t["avg_popularity"] = round(float(pop.mean()), 1)
        t["pop_zero_exact"] = int((pop == 0).sum())
        bins = list(range(0, 101, 10))
        labels = [f"{bins[i]}–{bins[i + 1]}" for i in range(len(bins) - 1)]
        vals = pop.dropna().astype(int).clip(0, 100)
        bucket_idx = (vals // 10).clip(upper=9)
        hist_counts = bucket_idx.value_counts().reindex(range(10), fill_value=0).sort_index()
        chart["pop_bins"] = labels
        chart["pop_hist"] = [int(x) for x in hist_counts.values]

    has_genre = "Genre" in df.columns and df["Genre"].notna().any()
    subgenre_counts = None
    if has_genre:
        genre_counts = df["Genre"].value_counts()
        t["dominant_genre"] = str(genre_counts.index[0])
        t["dominant_genre_pct"] = round(100 * genre_counts.iloc[0] / len(df), 1)
        if len(genre_counts) > 1:
            t["second_genre"] = str(genre_counts.index[1])
            t["second_genre_pct"] = round(100 * genre_counts.iloc[1] / len(df), 1)
        t["has_unknown_genre"] = bool("Unknown" in genre_counts.index)
        chart["genres"] = [str(x) for x in genre_counts.index]
        chart["genre_counts"] = [int(x) for x in genre_counts.values]

        subgenre_cols = [c for c in df.columns if c.startswith("Subgenre_")]
        if subgenre_cols:
            rows = df.loc[df["Genre"] == t["dominant_genre"], subgenre_cols]
            values = pd.concat([rows[c] for c in subgenre_cols]).dropna()
            values = values[values.astype(str).str.strip() != ""]
            if t["dominant_genre"] == "Rock":
                # Raw Discogs style tags under Rock are notoriously granular
                # (e.g. "Pop" and "Pop Rock" both show up for what's really
                # the same flavor) — collapse with the same bucketing used
                # elsewhere, display-only (never touches Subgenre_1/2/3).
                values = values.map(categorize_rock_subgenre)
            genre_words = _genre_name_words(t["dominant_genre"])
            values = values[~values.map(lambda v: _is_genre_name_restatement(v, genre_words))]
            counts = values.value_counts()
            # Only worth a chart if the dominant genre actually breaks down
            # into more than one distinct subgenre.
            if len(counts) >= 2:
                subgenre_counts = counts
                chart["subgenres"] = [str(x) for x in counts.index]
                chart["subgenre_counts"] = [int(x) for x in counts.values]
                chart["subgenre_total"] = int(counts.sum())

    top_artists = artist_counts.head(10)
    chart["top_artists"] = [str(x) for x in top_artists.index]
    chart["top_artist_counts"] = [int(x) for x in top_artists.values]

    has_dates = "Date" in df.columns
    dates = pd.to_datetime(df["Date"], errors="coerce", format="mixed") if has_dates else None
    has_dates = bool(has_dates and dates.notna().any())
    if has_dates:
        valid_dates = dates.dropna()
        t["unique_days"] = int(valid_dates.dt.date.nunique())
        t["date_min_display"] = _fmt_date(valid_dates.min())
        t["date_max_display"] = _fmt_date(valid_dates.max())

        periods = valid_dates.dt.to_period("M").astype(str)
        month_counts = periods.value_counts().sort_index()
        if len(month_counts) >= 2:
            chart["months"] = list(month_counts.index)
            chart["monthly_counts"] = [int(v) for v in month_counts.values]
            peak_period = month_counts.idxmax()
            trough_period = month_counts.idxmin()
            chart["peak_month_index"] = list(month_counts.index).index(peak_period)
            t["peak_month_label"] = _month_label(peak_period)
            t["peak_month_label_full"] = _month_label(peak_period, full=True)
            t["peak_month_count"] = int(month_counts.loc[peak_period])
            t["trough_month_label"] = _month_label(trough_period)
            t["trough_month_count"] = int(month_counts.loc[trough_period])
            covered = int(month_counts.sum())
            if covered < len(df):
                t["monthly_coverage_note"] = (
                    f"Based on the {covered:,} of {len(df):,} logged rows with a fully "
                    f"dated entry — rows logged with only a day/month and no year "
                    f"aren't included here."
                )

    has_year = "Release Year" in df.columns
    year = pd.to_numeric(df["Release Year"], errors="coerce") if has_year else None
    has_year = bool(has_year and year.notna().any())
    if has_year:
        yr = year.dropna().astype(int)
        decade = (yr // 10) * 10
        decade_counts = decade.value_counts().sort_index()
        chart["decades"] = [f"{int(x)}s" for x in decade_counts.index]
        chart["decade_counts"] = [int(x) for x in decade_counts.values]
        t["decade_min"] = int(decade_counts.index.min())
        t["decade_max"] = int(decade_counts.index.max())
        t["dominant_decade"] = int(decade_counts.idxmax())

        oldest_idx = yr.idxmin()
        newest_year = int(yr.max())
        oldest = {
            "title": str(df.loc[oldest_idx, "Song Title"]),
            "artist": str(df.loc[oldest_idx, "Artist Name"]),
            "year": int(yr.loc[oldest_idx]),
        }
        if has_dates and pd.notna(dates.loc[oldest_idx]):
            oldest["date_display"] = _fmt_date(dates.loc[oldest_idx])
        t["oldest_song"] = oldest

        year_counts = yr.value_counts()
        top_year_count = int(year_counts.max())
        top_years = sorted(year_counts[year_counts == top_year_count].index.tolist(), reverse=True)
        t["top_release_year_display"] = _format_tier([str(y) for y in top_years], top_year_count)

    has_country = "Country" in df.columns and df["Country"].notna().any()
    top_countries_intl = []
    if has_country:
        country_counts = df["Country"].value_counts()
        t["unique_countries"] = int(len(country_counts))
        top_code = str(country_counts.index[0])
        t["top_country_code"] = top_code

        world = _load_world_geometry()
        by_alpha2 = {f["properties"]["alpha2"]: f for f in world["features"] if f["properties"].get("alpha2")}

        top_feat = by_alpha2.get(top_code.upper())
        t["top_country_name"] = top_feat["properties"]["name"] if top_feat else top_code

        total_with_country = int(country_counts.sum())
        t["pct_outside_top"] = (
            round(100 * (total_with_country - int(country_counts.iloc[0])) / total_with_country, 1)
            if total_with_country
            else 0.0
        )

        counts_by_code = {str(k).upper(): int(v) for k, v in country_counts.items()}
        geo = json.loads(json.dumps(world))  # deep copy before mutating counts
        for feat in geo["features"]:
            a2 = feat["properties"].get("alpha2")
            feat["properties"]["count"] = counts_by_code.get(a2, 0) if a2 else 0

        intl = country_counts.drop(index=top_code)
        for code, count in intl.head(10).items():
            feat = by_alpha2.get(str(code).upper())
            name = feat["properties"]["name"] if feat else str(code)
            top_countries_intl.append({"name": name, "count": int(count)})

    return {"text": t, "chart": chart, "geo": geo, "top_countries_intl": top_countries_intl}


# ----------------------------------------------------------------------
# HTML report assembly
# ----------------------------------------------------------------------
_DASHBOARD_CSS = """
:root{
  --paper:#EDE7D9; --paper-dim:#E2DBC9; --ink:#211D1A; --ink-soft:#4A443E;
  --orange:#F2541D; --teal:#146B64; --teal-deep:#0B3F3B; --yellow:#F2B705;
  --pink:#C13C6B; --line:rgba(33,29,26,0.16); --card:#F5F0E5;
}
*{box-sizing:border-box;}
html,body{margin:0;padding:0;}
body{background:var(--paper); color:var(--ink); font-family:'Work Sans',sans-serif; line-height:1.5; -webkit-font-smoothing:antialiased;}
.wrap{max-width:1180px;margin:0 auto;padding:0 28px;}
.eyebrow{font-family:'JetBrains Mono',monospace; font-size:11px; letter-spacing:.14em; text-transform:uppercase; color:var(--ink-soft); font-weight:700;}
.eyebrow.accent{color:var(--orange);}
.eyebrow.teal{color:var(--teal);}
.sprockets{height:14px; background-image:radial-gradient(circle, var(--paper) 3.4px, transparent 3.6px); background-size:22px 14px; background-position:11px 0; background-color:var(--ink);}
.sprockets.bottom{transform:scaleY(-1);}
header.masthead{background:var(--ink); color:var(--paper);}
.masthead-inner{max-width:1180px;margin:0 auto;padding:38px 28px 34px;}
.masthead-top{display:flex; justify-content:space-between; align-items:flex-start; gap:20px; flex-wrap:wrap;}
.station-mark{font-family:'JetBrains Mono',monospace; font-size:11px; letter-spacing:.14em; text-transform:uppercase; color:var(--yellow); font-weight:700; border:1px solid var(--yellow); padding:5px 10px; border-radius:2px; white-space:nowrap;}
h1.title{font-family:'Anton',sans-serif; font-weight:400; font-size:clamp(56px,10vw,108px); line-height:.86; margin:14px 0 6px; letter-spacing:.005em; color:var(--paper);}
h1.title span{color:var(--orange);}
.masthead-sub{font-size:17px; max-width:660px; color:#D8D0BE; margin-top:8px;}
.masthead-meta{font-family:'JetBrains Mono',monospace; font-size:12.5px; color:#B9AE97; margin-top:18px; display:flex; gap:22px; flex-wrap:wrap;}
.masthead-meta b{color:var(--paper);}
.kpi-strip{display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:1px; background:var(--ink); margin-top:26px; border-radius:3px; overflow:hidden;}
.kpi{background:#2A2521; padding:18px 16px; text-align:left; position:relative;}
.kpi .num{font-family:'Anton',sans-serif; font-size:clamp(24px,3.2vw,34px); color:var(--paper); line-height:1;}
.kpi .lbl{font-family:'JetBrains Mono',monospace; font-size:10.5px; letter-spacing:.08em; text-transform:uppercase; color:#9A9082; margin-top:6px;}
section{padding:64px 0;}
section.alt{background:var(--paper-dim);}
.section-head{margin-bottom:30px; max-width:720px;}
h2.h{font-family:'Anton',sans-serif; font-weight:400; font-size:clamp(28px,4vw,42px); margin:8px 0 10px; line-height:1.02;}
.section-head p{color:var(--ink-soft); font-size:15.5px; max-width:640px;}
.tear{border:none; border-top:2px dashed var(--line); margin:0;}
.grid{display:grid; gap:18px;}
.g2{grid-template-columns:1fr 1fr;}
.g3{grid-template-columns:repeat(3,1fr);}
.gauto{grid-template-columns:repeat(auto-fit,minmax(220px,1fr));}
.card{background:var(--card); border:1px solid var(--line); border-radius:4px; padding:22px;}
.chart-card{background:var(--card); border:1px solid var(--line); border-radius:4px; padding:24px 24px 12px;}
.chart-card .chart-title{font-family:'JetBrains Mono',monospace; font-size:11.5px; text-transform:uppercase; letter-spacing:.08em; color:var(--ink-soft); font-weight:700; margin-bottom:14px; display:flex; justify-content:space-between; align-items:center;}
canvas{max-width:100%;}
.chart-box{position:relative; width:100%; height:280px;}
.chart-box.tall{height:340px;}
.catalog-card{background:var(--card); border:1px solid var(--line); border-radius:4px; padding:24px 22px; position:relative; overflow:hidden;}
.catalog-card::before{content:""; position:absolute; top:14px; left:0; width:100%; border-top:1px solid var(--line);}
.catalog-card .punch{position:absolute; top:6px; left:22px; width:11px; height:11px; border-radius:50%; background:var(--paper-dim); border:1px solid var(--line);}
.catalog-card .punch.r{left:auto; right:22px;}
.catalog-card .tag{font-family:'JetBrains Mono',monospace; font-size:10.5px; text-transform:uppercase; letter-spacing:.1em; font-weight:700; margin-top:16px;}
.catalog-card .tag.artist{color:var(--orange);}
.catalog-card .tag.song{color:var(--teal);}
.catalog-card .tag.album{color:var(--pink);}
.catalog-card .big{font-family:'Anton',sans-serif; font-size:26px; margin:8px 0 2px; line-height:1.05;}
.catalog-card .who{font-size:14.5px; color:var(--ink-soft);}
.catalog-card .count{font-family:'JetBrains Mono',monospace; font-size:13px; margin-top:12px; color:var(--ink);}
.catalog-card .count b{font-size:17px;}
.insight{background:var(--card); border:1px solid var(--line); border-radius:4px; padding:20px 20px 22px; display:flex; flex-direction:column; gap:8px;}
.insight .n{font-family:'JetBrains Mono',monospace; font-size:11px; color:var(--orange); font-weight:700; letter-spacing:.06em;}
.insight h3{font-family:'Anton',sans-serif; font-weight:400; font-size:20px; margin:0; line-height:1.12;}
.insight p{font-size:14px; color:var(--ink-soft); margin:0;}
.map-wrap{position:relative;}
#map{min-height:280px;}
#map svg{width:100%; height:auto; display:block;}
.country-path{stroke:var(--paper); stroke-width:.5; cursor:default;}
.map-tooltip{position:absolute; pointer-events:none; background:var(--ink); color:var(--paper); font-family:'JetBrains Mono',monospace; font-size:12px; padding:8px 11px; border-radius:3px; opacity:0; transition:opacity .1s ease; z-index:10; white-space:nowrap; box-shadow:0 6px 18px rgba(0,0,0,.25);}
.map-tooltip b{color:var(--yellow);}
.map-legend{display:flex; align-items:center; gap:10px; margin-top:14px; font-family:'JetBrains Mono',monospace; font-size:11px; color:var(--ink-soft);}
.legend-swatch{height:10px; width:120px; border-radius:2px; background:linear-gradient(90deg, #E2DBC9, #0B3F3B);}
.country-list{list-style:none; margin:0; padding:0;}
.country-list li{display:flex; justify-content:space-between; padding:8px 0; border-bottom:1px solid var(--line); font-size:14px;}
.country-list li .c-count{font-family:'JetBrains Mono',monospace; font-weight:700;}
footer{padding:44px 0 60px;}
footer p{font-size:13px; color:var(--ink-soft); max-width:760px;}
footer .fmark{font-family:'JetBrains Mono',monospace; font-size:11px; letter-spacing:.1em; text-transform:uppercase; color:var(--ink-soft); margin-bottom:10px;}
@media(max-width:920px){ .g2,.g3{grid-template-columns:1fr 1fr;} }
@media(max-width:600px){
  .g2,.g3{grid-template-columns:1fr;}
  .masthead-meta{gap:12px 18px;}
  .wrap{padding:0 18px;}
  .masthead-inner{padding:28px 18px 24px;}
  section{padding:40px 0;}
  .chart-card .chart-title{flex-direction:column; align-items:flex-start; gap:4px;}
  .map-legend{flex-wrap:wrap;}
  .kpi-strip{grid-template-columns:repeat(auto-fit,minmax(120px,1fr));}
  .chart-box.tall{height:260px;}
}
"""

_DASHBOARD_HEAD = """<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Anton&family=JetBrains+Mono:wght@400;500;700&family=Work+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>{css}</style>"""

# Client-side rendering: consumes the small CHART_DATA blob (and GEO, when
# present) to draw the Chart.js charts and D3 choropleth. Every chart is
# guarded on its canvas actually existing on the page, since a section with
# no underlying data isn't rendered server-side at all.
_DASHBOARD_JS = """
const CHART_DATA = JSON.parse(document.getElementById('chart-data').textContent);
const GEO_EL = document.getElementById('geo-data');
const GEO = GEO_EL ? JSON.parse(GEO_EL.textContent) : null;

if (typeof Chart === 'undefined' || typeof d3 === 'undefined') {
  const warn = document.createElement('div');
  warn.style.cssText = 'background:#F2541D;color:#fff;padding:14px 20px;font-family:monospace;font-size:13px;text-align:center;';
  warn.textContent = 'Chart library failed to load from CDN (offline or blocked) — charts below will not render. Try reloading with an internet connection.';
  document.body.insertBefore(warn, document.body.firstChild);
}

const fmt = n => n.toLocaleString('en-US');
Chart.defaults.font.family = "'Work Sans', sans-serif";
Chart.defaults.color = '#4A443E';
const INK='#211D1A', ORANGE='#F2541D', TEAL='#146B64', GRID='rgba(33,29,26,0.08)';
const PIE_PALETTE = [ORANGE, TEAL, '#F2B705', '#C13C6B', '#6B8F71', '#8C6E4A', '#5B7C99', '#B85C38', '#7B5EA7', '#4A7C6B'];

function bucketOther(labels, counts, total, threshFrac){
  const THRESH = threshFrac*total;
  let main=[], otherSum=0;
  labels.forEach((g,i)=>{ if(counts[i] < THRESH) otherSum += counts[i]; else main.push([g, counts[i]]); });
  if(otherSum>0) main.push(['Other', otherSum]);
  main.sort((a,b)=>b[1]-a[1]);
  return main;
}
function pieColors(labels){
  let idx=0;
  return labels.map(l => l==='Other' ? '#B9AE97' : PIE_PALETTE[(idx++)%PIE_PALETTE.length]);
}
const pieLegendOpts = { position:'right', labels:{ boxWidth:12, padding:10, font:{size:11}, color:INK } };

if (document.getElementById('chart-months') && CHART_DATA.months) {
  const monthLabels = CHART_DATA.months.map(m=>{
    const [y,mo]=m.split('-'); const d=new Date(parseInt(y),parseInt(mo)-1,1);
    return d.toLocaleString('en-US',{month:'short'})+" '"+String(y).slice(2);
  });
  new Chart(document.getElementById('chart-months'), {
    type:'bar',
    data:{ labels:monthLabels, datasets:[{
      data:CHART_DATA.monthly_counts,
      backgroundColor: CHART_DATA.monthly_counts.map((v,i)=> i===CHART_DATA.peak_month_index ? ORANGE : TEAL),
      borderRadius:3, maxBarThickness:56
    }]},
    options:{
      responsive:true, maintainAspectRatio:false,
      plugins:{legend:{display:false}, tooltip:{callbacks:{label:c=>fmt(c.parsed.y)+' tracks logged'}}},
      scales:{ y:{grid:{color:GRID}, ticks:{callback:v=>fmt(v)}}, x:{grid:{display:false}} }
    }
  });
}

if (document.getElementById('chart-popularity') && CHART_DATA.pop_bins) {
  const popPeak = CHART_DATA.pop_hist.indexOf(Math.max(...CHART_DATA.pop_hist));
  new Chart(document.getElementById('chart-popularity'), {
    type:'bar',
    data:{ labels:CHART_DATA.pop_bins, datasets:[{
      data:CHART_DATA.pop_hist, backgroundColor: CHART_DATA.pop_bins.map((b,i)=> i===popPeak?ORANGE:TEAL), borderRadius:3, maxBarThickness:52
    }]},
    options:{
      responsive:true, maintainAspectRatio:false,
      plugins:{legend:{display:false}, tooltip:{callbacks:{label:c=>fmt(c.parsed.y)+' tracks'}}},
      scales:{ y:{grid:{color:GRID}, ticks:{callback:v=>fmt(v)}}, x:{grid:{display:false}, title:{display:true,text:'Popularity score (0–100)',font:{size:11}}} }
    }
  });
}

if (document.getElementById('chart-genre') && CHART_DATA.genres) {
  const main = bucketOther(CHART_DATA.genres, CHART_DATA.genre_counts, CHART_DATA.total_plays, 0.01);
  const labels = main.map(m=>m[0]);
  new Chart(document.getElementById('chart-genre'), {
    type:'pie',
    data:{ labels, datasets:[{ data:main.map(m=>m[1]), backgroundColor:pieColors(labels), borderColor:'#F5F0E5', borderWidth:2 }]},
    options:{
      responsive:true, maintainAspectRatio:false,
      plugins:{ legend: pieLegendOpts, tooltip:{callbacks:{label:c=>c.label+': '+fmt(c.parsed)+' tracks ('+(c.parsed/CHART_DATA.total_plays*100).toFixed(1)+'%)'}} }
    }
  });
}

if (document.getElementById('chart-subgenre') && CHART_DATA.subgenres) {
  const main = bucketOther(CHART_DATA.subgenres, CHART_DATA.subgenre_counts, CHART_DATA.subgenre_total, 0.02);
  const labels = main.map(m=>m[0]);
  new Chart(document.getElementById('chart-subgenre'), {
    type:'pie',
    data:{ labels, datasets:[{ data:main.map(m=>m[1]), backgroundColor:pieColors(labels), borderColor:'#F5F0E5', borderWidth:2 }]},
    options:{
      responsive:true, maintainAspectRatio:false,
      plugins:{ legend: pieLegendOpts, tooltip:{callbacks:{label:c=>c.label+': '+fmt(c.parsed)+' tracks ('+(c.parsed/CHART_DATA.subgenre_total*100).toFixed(1)+'%)'}} }
    }
  });
}

if (document.getElementById('chart-artists') && CHART_DATA.top_artists) {
  new Chart(document.getElementById('chart-artists'), {
    type:'bar',
    data:{ labels:CHART_DATA.top_artists, datasets:[{ data:CHART_DATA.top_artist_counts, backgroundColor:TEAL, borderRadius:3 }]},
    options:{
      responsive:true, maintainAspectRatio:false, indexAxis:'y',
      plugins:{legend:{display:false}, tooltip:{callbacks:{label:c=>fmt(c.parsed.x)+' plays'}}},
      scales:{ x:{grid:{color:GRID}}, y:{grid:{display:false}} }
    }
  });
}

if (document.getElementById('chart-decades') && CHART_DATA.decades) {
  const peak = CHART_DATA.decade_counts.indexOf(Math.max(...CHART_DATA.decade_counts));
  new Chart(document.getElementById('chart-decades'), {
    type:'bar',
    data:{ labels:CHART_DATA.decades, datasets:[{ data:CHART_DATA.decade_counts, backgroundColor: CHART_DATA.decades.map((d,i)=>i===peak?ORANGE:TEAL), borderRadius:3, maxBarThickness:60 }]},
    options:{
      responsive:true, maintainAspectRatio:false,
      plugins:{legend:{display:false}, tooltip:{callbacks:{label:c=>fmt(c.parsed.y)+' tracks'}}},
      scales:{ y:{grid:{color:GRID}, ticks:{callback:v=>fmt(v)}}, x:{grid:{display:false}} }
    }
  });
}

if (document.getElementById('map') && GEO) {
  const container = document.getElementById('map');
  const width = container.clientWidth || 700;
  const height = width*0.52;
  const svg = d3.select(container).append('svg').attr('viewBox', `0 0 ${width} ${height}`);
  // Antarctica is never a real "artist country of origin" — it's always
  // empty, but its landmass is huge, so fitting the projection to the full
  // GEO collection (Antarctica included) wastes most of the map's height on
  // it and shrinks everything else. Excluding it from both the fit and the
  // render gives a much better zoom on the countries that actually matter.
  const mapFeatures = GEO.features.filter(f => f.id !== 'ATA');
  const mapGeo = {type: 'FeatureCollection', features: mapFeatures};
  const projection = d3.geoNaturalEarth1().fitSize([width, height], mapGeo);
  const path = d3.geoPath().projection(projection);
  const maxCount = d3.max(mapFeatures, d=>d.properties.count||0);
  const colorScale = d3.scaleSequential(t=>d3.interpolate('#E2DBC9','#0B3F3B')(t)).domain([0, Math.log(maxCount+1)]);
  const tooltip = document.getElementById('map-tooltip');
  const wrapEl = document.querySelector('.map-wrap');

  // Shared by mouse hover and touch — event.touches[0] carries the
  // coordinates on a touch event, event itself does on a mouse event.
  let lastTouchedEl = null;
  function showTooltip(event, d, el){
    const rect = wrapEl.getBoundingClientRect();
    const point = event.touches ? event.touches[0] : event;
    const cnt = d.properties.count||0;
    tooltip.innerHTML = `${d.properties.name}<br><b>${cnt.toLocaleString('en-US')}</b> ${cnt===1?'play':'plays'}`;
    tooltip.style.left = (point.clientX - rect.left + 14) + 'px';
    tooltip.style.top = (point.clientY - rect.top + 10) + 'px';
    tooltip.style.opacity = 1;
    d3.select(el).attr('stroke', '#211D1A').attr('stroke-width', 1.1);
    lastTouchedEl = el;
  }
  function hideTooltip(){
    tooltip.style.opacity = 0;
    if (lastTouchedEl) d3.select(lastTouchedEl).attr('stroke','#EDE7D9').attr('stroke-width',.5);
    lastTouchedEl = null;
  }

  svg.selectAll('path').data(mapFeatures).join('path')
    .attr('class','country-path')
    .attr('d', path)
    .attr('fill', d => d.properties.count>0 ? colorScale(Math.log(d.properties.count+1)) : '#DCD4C0')
    .on('mousemove', function(event, d){ showTooltip(event, d, this); })
    .on('mouseleave', hideTooltip)
    .on('touchstart', function(event, d){ event.preventDefault(); showTooltip(event, d, this); })
    .on('touchmove', function(event, d){ event.preventDefault(); showTooltip(event, d, this); });

  // Touch has no "moved the mouse away" equivalent — dismiss the tooltip
  // when a tap lands anywhere outside a country shape.
  document.addEventListener('touchstart', function(event){
    if (!event.target.closest('.country-path')) hideTooltip();
  });
}
"""


def _esc(value) -> str:
    return html.escape(str(value))


def _kpi_tile(value, label: str) -> str:
    return f'<div class="kpi"><div class="num">{_esc(value)}</div><div class="lbl">{_esc(label)}</div></div>'


def _catalog_card(tag_class: str, tag: str, big: str, who: str | None, count_html: str) -> str:
    who_html = f'<div class="who">{_esc(who)}</div>' if who else ""
    return (
        '<div class="catalog-card"><span class="punch"></span><span class="punch r"></span>'
        f'<div class="tag {tag_class}">{_esc(tag)}</div>'
        f'<div class="big">{_esc(big)}</div>{who_html}'
        f'<div class="count">{count_html}</div></div>'
    )


def _insight_card(tag: str, headline: str, body: str) -> str:
    return (
        '<div class="insight">'
        f'<span class="n">{_esc(tag)}</span><h3>{_esc(headline)}</h3><p>{_esc(body)}</p>'
        "</div>"
    )


def build_html_report(df: pd.DataFrame) -> str:
    """Return a self-contained magazine-style dashboard summarizing the
    enriched log — masthead/KPI strip, most-played cards, and (only when the
    underlying column has usable data) monthly, popularity, genre/subgenre,
    top-artist, decade, and world-map sections. Sections without enough data
    are omitted outright rather than shown broken or empty.
    """
    data = _dashboard_data(df)
    t, chart, geo, top_countries_intl = data["text"], data["chart"], data["geo"], data["top_countries_intl"]

    # ---- masthead ----
    meta_spans = []
    if "date_min_display" in t:
        meta_spans.append(f"<span>SESSION RANGE&nbsp; <b>{_esc(t['date_min_display'])} → {_esc(t['date_max_display'])}</b></span>")
    if "unique_days" in t:
        meta_spans.append(f"<span>LOGGED DAYS ON AIR&nbsp; <b>{t['unique_days']}</b></span>")
    if "avg_popularity" in t:
        meta_spans.append(f"<span>AVG. TRACK POPULARITY&nbsp; <b>{t['avg_popularity']}</b>/100</span>")
    masthead_meta = f'<div class="masthead-meta">{"".join(meta_spans)}</div>' if meta_spans else ""

    kpis = [_kpi_tile(f"{t['total_plays']:,}", "Total Plays"), _kpi_tile(f"{t['unique_songs']:,}", "Unique Songs"),
            _kpi_tile(f"{t['unique_artists']:,}", "Unique Artists")]
    if "unique_albums" in t:
        kpis.append(_kpi_tile(f"{t['unique_albums']:,}", "Unique Albums"))
    if "unique_countries" in t:
        kpis.append(_kpi_tile(t["unique_countries"], "Countries Repped"))

    masthead = f"""
<div class="sprockets"></div>
<header class="masthead">
  <div class="masthead-inner">
    <div class="masthead-top">
      <div>
        <span class="station-mark">ON-AIR LOG &middot; COMPLETE SESSIONS</span>
        <h1 class="title">ROTATION<span>.</span></h1>
      </div>
    </div>
    {masthead_meta}
    <div class="kpi-strip">{"".join(kpis)}</div>
  </div>
</header>
<div class="sprockets bottom"></div>
"""

    sections = []

    # ---- most played ----
    cards = []
    if "most_played_artist" in t:
        a = t["most_played_artist"]
        cards.append(_catalog_card("artist", "Most-played artist", a["name"], None, f'<b>{a["count"]}</b> plays logged'))
    if "most_played_song" in t:
        s = t["most_played_song"]
        cards.append(_catalog_card("song", "Most-played song", f'“{s["title"]}”', s["artist"], f'<b>{s["count"]}</b> plays logged'))
    if "most_played_album" in t:
        al = t["most_played_album"]
        cards.append(_catalog_card("album", "Most-played album", al["title"], al["artist"], f'<b>{al["count"]}</b> tracks logged from it'))
    if cards:
        grid_class = "g3" if len(cards) == 3 else "gauto"
        sections.append(f"""
  <section>
    <div class="section-head">
      <span class="eyebrow accent">Top Stats</span>
      <h2 class="h">Most Played</h2>
    </div>
    <div class="grid {grid_class}">{"".join(cards)}</div>
  </section>
  <hr class="tear">""")

    # ---- tracks over time ----
    if "months" in chart:
        note = f"Peak: {t['peak_month_label']} ({t['peak_month_count']}) · Quietest: {t['trough_month_label']} ({t['trough_month_count']})"
        coverage = f'<p style="margin-top:10px;font-size:13px;">{_esc(t["monthly_coverage_note"])}</p>' if "monthly_coverage_note" in t else ""
        sections.append(f"""
  <section>
    <div class="section-head">
      <span class="eyebrow teal">Over time</span>
      <h2 class="h">Songs Played Per Month</h2>
      <p>{_esc(t['peak_month_label_full'])} was the busiest month logged, with {t['peak_month_count']} tracks.</p>
    </div>
    <div class="chart-card">
      <div class="chart-title"><span>Tracks logged per month</span><span>{_esc(note)}</span></div>
      <div class="chart-box"><canvas id="chart-months"></canvas></div>
    </div>
    {coverage}
  </section>
  <hr class="tear">""")

    # ---- popularity ----
    if "avg_popularity" in t:
        sections.append(f"""
  <section class="alt">
    <div class="section-head">
      <span class="eyebrow accent">Mainstream vs. Underground</span>
      <h2 class="h">How popular are our picks?</h2>
      <p>Spotify gives each track a popularity score from 0–100. Our average was {t['avg_popularity']}/100.</p>
    </div>
    <div class="chart-card">
      <div class="chart-title"><span>Popularity score distribution</span></div>
      <div class="chart-box tall"><canvas id="chart-popularity"></canvas></div>
    </div>
  </section>
  <hr class="tear">""")

    # ---- rotation mix (genre pie + subgenre pie + top artists) ----
    mix_blocks = []
    if "dominant_genre" in t:
        mix_blocks.append('<div class="chart-card"><div class="chart-title"><span>Tracks by genre</span></div><div class="chart-box tall"><canvas id="chart-genre"></canvas></div></div>')
    if "subgenres" in chart:
        mix_blocks.append(
            f'<div class="chart-card"><div class="chart-title"><span>{_esc(t["dominant_genre"])}\'s subgenres</span>'
            f'<span>of {chart["subgenre_total"]:,} {_esc(t["dominant_genre"]).lower()} tracks</span></div>'
            '<div class="chart-box tall"><canvas id="chart-subgenre"></canvas></div></div>'
        )
    mix_grid = f'<div class="grid g2">{"".join(mix_blocks)}</div>' if mix_blocks else ""

    if "dominant_genre" in t:
        second_bit = f", with {_esc(t['second_genre'])} the runner-up at {t['second_genre_pct']}%" if "second_genre" in t else ""
        unknown_bit = ' "Unknown" is a genuine tag in the data — tracks metadata services couldn\'t classify — not a gap in the logging.' if t.get("has_unknown_genre") else ""
        mix_intro = f"{_esc(t['dominant_genre'])} leads the log at {t['dominant_genre_pct']}% of all tracks{second_bit}.{unknown_bit}"
    else:
        mix_intro = "The most-played artists in this log, ranked by plays."

    sections.append(f"""
  <section>
    <div class="section-head">
      <h2 class="h">Top Genres &amp; Artists</h2>
      <p>{mix_intro}</p>
    </div>
    {mix_grid}
    <div class="chart-card" style="margin-top:18px;">
      <div class="chart-title"><span>Most-played artists</span></div>
      <div class="chart-box tall"><canvas id="chart-artists"></canvas></div>
    </div>
  </section>
  <hr class="tear">""")

    # ---- decades ----
    if "dominant_decade" in t:
        oldest = t["oldest_song"]
        oldest_detail = f"by {_esc(oldest['artist'])}"
        if "date_display" in oldest:
            oldest_detail += f", logged {_esc(oldest['date_display'])}"
        oldest_detail += "."
        sections.append(f"""
  <section class="alt">
    <div class="section-head">
      <span class="eyebrow accent">Release years</span>
      <h2 class="h">Release Years</h2>
      <p>Release years in this log span the {t['decade_min']}s to the {t['decade_max']}s, with the {t['dominant_decade']}s dominating the catalog.</p>
    </div>
    <div class="chart-card">
      <div class="chart-title"><span>Tracks logged by decade of release</span></div>
      <div class="chart-box"><canvas id="chart-decades"></canvas></div>
    </div>
    <div class="grid g2" style="margin-top:18px;">
      <div class="insight"><span class="n">OLDEST ON RECORD</span><h3>{_esc(oldest['title'])} — {oldest['year']}</h3><p>{oldest_detail}</p></div>
      <div class="insight"><span class="n">MOST-PLAYED YEAR</span><h3>{_esc(t['top_release_year_display'])}</h3><p>the release year with the most tracks logged in this run.</p></div>
    </div>
  </section>
  <hr class="tear">""")

    # ---- world map ----
    if "top_country_code" in t:
        world_copy = (
            f"{t['pct_outside_top']}% of every track with a known artist country came from outside "
            f"{_esc(t['top_country_name'])} — {t['unique_countries']} countries in total."
        )
        country_list_items = "".join(
            f'<li><span class="c-name">{_esc(c["name"])}</span><span class="c-count">{c["count"]:,}</span></li>'
            for c in top_countries_intl
        )
        sections.append(f"""
  <section>
    <div class="section-head">
      <span class="eyebrow teal">Beyond the dial</span>
      <h2 class="h">Around the world</h2>
      <p>{world_copy}</p>
    </div>
    <div class="grid g2" style="align-items:start;">
      <div class="chart-card map-wrap">
        <div class="chart-title"><span>Tracks by artist country of origin</span><span>hover to explore</span></div>
        <div id="map"></div>
        <div class="map-legend"><span>Fewer plays</span><div class="legend-swatch"></div><span>More plays (log scale)</span></div>
        <div class="map-tooltip" id="map-tooltip"></div>
      </div>
      <div class="chart-card">
        <div class="chart-title"><span>Top countries outside {_esc(t['top_country_name'])}</span></div>
        <ul class="country-list">{country_list_items}</ul>
      </div>
    </div>
  </section>
  <hr class="tear">""")

    if sections and sections[-1].rstrip().endswith('<hr class="tear">'):
        sections[-1] = sections[-1].rstrip()[: -len('\n  <hr class="tear">')]

    # ---- methodology ----
    included = ["Song Title, Artist Name"]
    if "avg_popularity" in t:
        included.append("Spotify popularity/release year")
    if "dominant_genre" in t:
        included.append("Discogs genre")
    if "top_country_code" in t:
        included.append("MusicBrainz artist country")
    methodology = (
        f"This report covers {t['total_plays']:,} logged plays"
        + (f" between {t['date_min_display']} and {t['date_max_display']}" if "date_min_display" in t else "")
        + f". Fields included this run: {', '.join(included)}. Album, popularity, release year, genre, and "
        "country come from streaming/catalog metadata matched to each track, so a handful of obscure or "
        "self-released tracks may be tagged “Unknown” or missing a field rather than misclassified."
    )

    safe_slash = "<\\/"
    chart_json = json.dumps(chart, ensure_ascii=False).replace("</", safe_slash)
    if geo:
        geo_json = json.dumps(geo, ensure_ascii=False).replace("</", safe_slash)
        geo_script = f'<script id="geo-data" type="application/json">{geo_json}</script>'
    else:
        geo_script = ""

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<title>Radio Log Report</title>
{_DASHBOARD_HEAD.format(css=_DASHBOARD_CSS)}
{masthead}
<main class="wrap">
{"".join(sections)}
</main>
<footer>
  <div class="wrap">
    <div class="fmark">Methodology &middot; generated {generated}</div>
    <p>{methodology}</p>
  </div>
</footer>
<script id="chart-data" type="application/json">{chart_json}</script>
{geo_script}
<script>{_DASHBOARD_JS}</script>
"""


def save_html_report(df: pd.DataFrame, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(build_html_report(df))
    print(f"Saved report to: {path}")
