"""Normalize raw Discogs genre/style tags into consistent buckets.

Discogs returns a mix of top-level genres (e.g. "Rock") and specific
styles (e.g. "Shoegaze", "Bossa Nova") with no guaranteed structure. These
helpers push every raw tag into one of Discogs' 15 top-level genres as the
main "Genre", keeping the more specific tag(s) as "Subgenre_N", and then
optionally collapse the huge spread of subgenres (especially under Rock)
into broader, chart-friendly categories.
"""

import pandas as pd

DISCOGS_TOP_LEVEL_GENRES = [
    "Blues", "Brass & Military", "Children's", "Classical",
    "Electronic", "Folk, World, & Country", "Funk / Soul",
    "Hip Hop", "Jazz", "Latin", "Non-Music", "Pop", "Reggae",
    "Rock", "Stage & Screen",
]

_GENRE_KEYWORD_MAP = [
    ("Folk, World, & Country", [
        "anti-folk", "salsa", "bossa nova", "cumbia", "native american",
        "bolero", "tejano", "folclor andino", "americana", "traditional country",
        "outlaw country", "alt country", "classic country", "bluegrass",
        "chanson québécoise", "corrido", "country", "malay", "opm",
        "singer-songwriter", "southern gothic",
    ]),
    ("Rock", [
        "rock", "metal", "garage", "new wave", "shoegaze", "punk",
        "deathcore", "djent", "midwest emo", "riot grrrl", "slowcore",
        "psychobilly", "hardcore", "grunge", "alternative", "indie rock",
        "jam band", "indie",
    ]),
    ("Pop", [
        "pop", "doo-wop", "shibuya-kei", "k-pop", "aor", "k-ballad",
    ]),
    ("Electronic", [
        "tech", "italo disco", "french house", "electroclash",
        "drum and bass", "afro house", "edm", "stutter house",
        "house", "rally house", "bass house", "electronic",
        "synth", "techno", "trance", "dubstep", "ambient",
    ]),
    ("Hip Hop", [
        "lo-fi", "lofi", "rap", "r&b", "hip hop", "miami bass",
        "trap", "drill", "grime",
    ]),
    ("Funk / Soul", [
        "soul", "funk", "motown", "neo soul", "neo-soul",
    ]),
    ("Jazz", [
        "jazz", "bebop", "swing", "big band", "smooth jazz", "avant-garde",
    ]),
    ("Latin", [
        "reggaeton", "latin indie", "neoperreo", "mexican indie",
        "latin alternative", "nova mpb", "amapiano", "latin",
    ]),
    ("Reggae", ["reggae", "ska", "dub", "dancehall"]),
    ("Classical", [
        "choral", "neoclassical", "medieval", "classical",
        "baroque", "opera", "symphony",
    ]),
    ("Blues", ["blues"]),
    ("Stage & Screen", [
        "anime", "musicals", "vgm", "soundtrack", "ost",
        "video game", "film score", "musical",
    ]),
    ("Non-Music", ["spoken word", "poetry", "audiobook", "comedy"]),
]

_FOLK_WORLD_COUNTRY_SPLIT_KEYWORDS = ("world", "country")

_ROCK_SUBGENRE_MAP = [
    ("Alternative/Indie Rock", [
        "alternative/indie rock", "indie rock", "alternative rock",
        "indie", "alternative", "lo-fi indie", "bedroom pop",
        "indie folk", "indie pop", "indie jazz", "indie r&b",
        "mexican indie", "latin indie", "japanese indie",
        "anti-folk", "egg punk", "emo", "midwest emo",
        "slowcore", "noise rock", "noise", "math rock",
    ]),
    ("Pop Rock", [
        "pop rock", "pop", "soft rock", "power pop",
        "jangle pop", "sunshine pop", "yacht rock",
        "arena rock", "aor", "bubblegum", "baroque pop",
        "alt-pop", "synth-pop", "synthpop", "dance-pop",
        "k-pop", "j-pop", "city pop", "k-ballad", "ballad",
        "vocal", "doo wop", "doo-wop", "t-pop", "c-pop",
        "mandopop", "cantopop", "kayōkyoku", "anison",
        "disco", "nu-disco", "eurodance", "europop",
        "italo disco", "italo dance", "reggae-pop",
    ]),
    ("Folk/Country Rock", [
        "folk/country rock", "folk rock", "country rock",
        "folk, world, & country", "folk", "southern rock",
        "americana", "alt country", "outlaw country",
        "southern gothic", "appalachian music", "bluegrass",
        "newgrass", "singer-songwriter", "acoustic",
        "country", "traditional country", "classic country",
        "country blues", "chanson", "chanson québécoise",
        "mpb", "nova mpb", "traditional music", "celtic",
        "native american music", "hillbilly", "honky tonk",
        "cajun", "folk pop", "yé-yé", "traditional folk", "neofolk",
    ]),
    ("Punk/Hardcore", [
        "punk/hardcore", "punk", "hardcore", "post-hardcore",
        "pop punk", "proto-punk", "skate punk", "riot grrrl",
        "britcore", "power violence", "beatdown", "grindcore",
        "goregrind", "pornogrind", "crust", "oi",
    ]),
    ("Hard Rock/Metal", [
        "hard rock/metal", "hard rock", "heavy metal", "metal",
        "doom metal", "death metal", "black metal", "thrash",
        "speed metal", "power metal", "groove metal", "funk metal",
        "rap metal", "trap metal", "folk metal", "viking metal",
        "symphonic metal", "gothic metal", "atmospheric black metal",
        "unblack metal", "stoner metal", "sludge metal",
        "technical death metal", "melodic death metal",
        "metalcore", "deathcore", "djent", "nu metal",
        "alternative metal", "industrial metal", "post-metal",
        "stoner rock",
    ]),
    ("Progressive/Art Rock", [
        "progressive/art rock", "prog rock", "progressive rock",
        "art rock", "post rock", "space rock", "symphonic rock",
        "rock opera", "krautrock", "experimental", "avantgarde",
        "jam band", "fusion", "jazz fusion", "progressive metal",
        "avant-garde jazz", "free improvisation", "idm",
        "glitch", "plunderphonics", "sound collage",
    ]),
    ("Classic Rock", [
        "classic rock", "rock & roll", "rock (general)",
        "rock", "beat", "mod", "roots rock", "pub rock", "acid rock",
    ]),
    ("Grunge/Shoegaze", [
        "grunge/shoegaze", "grunge", "shoegaze", "dream pop",
        "ethereal", "hypnagogic pop", "post-grunge",
        "chillwave", "ambient", "downtempo", "trip hop",
        "leftfield", "witch house", "coldwave", "darkwave", "cloud rap",
    ]),
    ("Psychedelic/Garage", [
        "psychedelic/garage", "psychedelic rock", "psychedelic",
        "garage rock", "surf", "psychobilly", "rockabilly", "twist",
    ]),
    ("Post-Punk/New Wave", [
        "post-punk/new wave", "post-punk", "new wave",
        "goth rock", "gothic rock", "cold wave", "ndw",
        "no wave", "gothic metal", "glam rock", "glam",
        "britpop", "madchester",
    ]),
    ("Blues Rock", [
        "blues rock", "blues", "electric blues",
        "modern electric blues", "piano blues", "delta blues",
        "chicago blues", "texas blues", "classic blues", "jazz blues",
    ]),
    ("International Rock", [
        "international rock", "j-rock", "k-rock", "mexican rock",
        "latin rock", "thai rock", "turkish", "malay",
        "pinoy indie", "opm",
    ]),
    ("Funk / Soul", ["jazz/fusion rock", "jazz-rock", "funk rock"]),
]

_SECONDARY_CATEGORY_MAP = [
    ("Funk / Soul", [
        "funk / soul", "funk", "soul", "rhythm & blues",
        "contemporary r&b", "r&b", "neo soul", "p.funk",
        "jazz-funk", "motown", "classic soul", "philly soul",
        "northern soul", "southern soul", "quiet storm",
        "jazz", "bebop", "bop", "swing", "big band",
        "smooth jazz", "cool jazz", "acid jazz", "soul-jazz",
        "contemporary jazz", "hard bop", "post bop",
        "vocal jazz", "latin jazz", "brazilian jazz",
        "french jazz", "future jazz", "nu jazz",
        "electronic", "house", "techno", "electro",
        "tech house", "deep house", "electro house",
        "progressive house", "tropical house", "afro house",
        "bass house", "french house", "garage house",
        "hip-house", "acid house", "hard house", "euro house",
        "stutter house", "rally house", "g-house", "future house",
        "trance", "dubstep", "drum n bass", "drum and bass",
        "jungle", "liquid funk", "breaks", "breakbeat",
        "broken beat", "uk garage", "bassline", "grime",
        "juke", "footwork", "bass music", "uk funky",
        "edm", "big beat", "electroclash", "industrial",
        "ebm", "minimal", "minimal techno", "dub techno",
        "gabber", "hardcore", "happy hardcore", "doomcore",
        "breakcore", "hard techno", "hard trance",
        "progressive trance", "goa trance", "hard beat",
        "rave", "new age", "chiptune", "synthwave",
        "vaporwave", "nightcore", "future bass",
        "deconstructed club", "jersey club", "3 step",
        "hip hop", "rap", "trap", "drill", "boom bap",
        "conscious", "gangsta", "hardcore hip-hop", "pop rap",
        "jazzy hip-hop", "jazz rap", "lo-fi", "cloud rap",
        "emo rap", "trap metal", "experimental hip hop",
        "east coast hip hop", "west coast hip hop",
        "southern hip hop", "g-funk", "hyphy", "crunk",
        "miami bass", "bounce", "screw", "horrorcore",
        "thug rap", "underground hip hop", "old school hip hop",
        "alternative hip hop", "abstract", "turntablism",
        "cut-up/dj", "ragga hiphop", "latin hip hop",
        "turkish hip hop", "k-rap", "melodic rap",
        "latin", "salsa", "cumbia", "bolero", "son",
        "son montuno", "son cubano", "guaguancó", "timba",
        "bachata", "merengue", "vallenato", "ranchera",
        "corrido", "corridos tumbados", "corridos bélicos",
        "norteño", "tejano", "banda", "mariachi",
        "música mexicana", "samba", "bossa nova", "bossanova",
        "mpb", "tropicália", "afrobeat", "highlife",
        "afro-cuban", "afro house", "amapiano", "afro tech",
        "reggae", "ska", "dub", "roots reggae", "dancehall",
        "ragga", "rocksteady", "reggaeton", "neoperreo",
        "dembow", "plena", "bomba", "gaita",
        "flamenco", "rumba", "danzon", "pachanga",
        "guajira", "huayno", "folclor andino",
        "cumbia sonidera", "electrocumbia", "tropical music",
        "variété française", "french pop", "french indie pop",
        "chanson française", "danish pop", "dansk pop",
        "egyptian pop", "thai pop", "turkish pop",
        "indian", "hindustani", "chinese", "taiwanese pop",
        "singaporean", "malaysian", "indonesian",
        "african", "caribbean", "world music",
    ]),
    ("Stage & Screen", [
        "stage & screen", "soundtrack", "score", "musical",
        "musicals", "theme", "opera", "operetta", "oratorio",
        "passion", "classical", "baroque", "romantic",
        "modern classical", "neoclassical", "contemporary",
        "choral", "brass band", "marches", "symphonic",
        "chamber", "medieval", "renaissance",
        "video game music", "vgm", "japanese vgm",
        "anime", "vocaloid", "anison", "j-r&b",
    ]),
    ("Non-Music", [
        "non-music", "interview", "comedy", "parody",
        "novelty", "karaoke", "spoken word", "poetry",
        "audiobook", "educational", "special effects",
        "movie effects", "sound effects", "field recording",
    ]),
    ("Holiday", ["christmas", "holiday", "gospel"]),
    ("Children's", ["children's"]),
]


def fix_genres(df: pd.DataFrame, genre_col: str = "Genre",
                subgenre_cols: tuple = ("Subgenre_1", "Subgenre_2", "Subgenre_3")) -> pd.DataFrame:
    """Reassign each row's main Genre to one of Discogs' 15 top-level genres,
    demoting whatever was previously in Genre into the subgenre columns.
    """
    genres = df.copy()

    for idx in genres.index:
        original_genre = genres.loc[idx, genre_col]
        if pd.isna(original_genre) or not original_genre:
            continue

        genre_lower = str(original_genre).lower()

        # Special case: "Folk, World, & Country" often arrives split across
        # Genre="Folk", Subgenre_1="World", Subgenre_2="& Country".
        if genre_lower == "folk":
            sub_cols = [c for c in subgenre_cols if c in genres.columns]
            sub_vals = [genres.loc[idx, c] if len(sub_cols) > i else None
                        for i, c in enumerate(sub_cols)]
            if len(sub_vals) >= 2 and pd.notna(sub_vals[0]) and pd.notna(sub_vals[1]) \
                    and "world" in str(sub_vals[0]).lower() and "country" in str(sub_vals[1]).lower():
                genres.loc[idx, genre_col] = "Folk, World, & Country"
                genres.loc[idx, sub_cols[0]] = None
                genres.loc[idx, sub_cols[1]] = None
                if len(sub_cols) >= 3 and pd.notna(genres.loc[idx, sub_cols[2]]):
                    genres.loc[idx, sub_cols[0]] = genres.loc[idx, sub_cols[2]]
                    genres.loc[idx, sub_cols[2]] = None
                continue

        replacement = None
        for top_genre, keywords in _GENRE_KEYWORD_MAP:
            if any(k in genre_lower for k in keywords):
                replacement = top_genre
                break

        if replacement and replacement != original_genre:
            sub_cols = [c for c in subgenre_cols if c in genres.columns]
            # Shift existing subgenres down to make room, then insert the
            # original (specific) genre as the new Subgenre_1.
            for i in range(len(sub_cols) - 1, 0, -1):
                if pd.notna(genres.loc[idx, sub_cols[i - 1]]):
                    genres.loc[idx, sub_cols[i]] = genres.loc[idx, sub_cols[i - 1]]
            if sub_cols:
                genres.loc[idx, sub_cols[0]] = original_genre
            genres.loc[idx, genre_col] = replacement

    return genres


def categorize_rock_subgenre(subgenre) -> str | None:
    """Bucket a single Rock subgenre tag into a broader Rock category."""
    if pd.isna(subgenre):
        return None
    subgenre_lower = str(subgenre).lower().strip()

    for category, keywords in _ROCK_SUBGENRE_MAP:
        if any(k in subgenre_lower for k in keywords):
            return category

    for category, keywords in _SECONDARY_CATEGORY_MAP:
        if any(k in subgenre_lower for k in keywords):
            return category

    return "Other"  # anything unmatched — don't silently misattribute it


def categorize_all_subgenres(df: pd.DataFrame,
                              subgenre_cols: tuple = ("Subgenre_1", "Subgenre_2", "Subgenre_3")) -> pd.DataFrame:
    """Apply categorize_rock_subgenre() to every subgenre column."""
    out = df.copy()
    for col in subgenre_cols:
        if col in out.columns:
            out[col] = out[col].apply(categorize_rock_subgenre)
    return out
