"""Configuration loader.

All credentials are read from environment variables (optionally loaded from
a local .env file via python-dotenv). Nothing sensitive should ever be
hard-coded here or committed to version control.
"""

import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # python-dotenv is optional; if it's not installed, we just rely on
    # variables already present in the environment.
    pass


REQUIRED_VARS = [
    "SPOTIFY_CLIENT_ID",
    "SPOTIFY_CLIENT_SECRET",
    "DISCOGS_TOKEN",
]

SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET")
DISCOGS_TOKEN = os.environ.get("DISCOGS_TOKEN")

MUSICBRAINZ_APP_NAME = os.environ.get("MUSICBRAINZ_APP_NAME", "RadioLogsAnalyzer/0.1")
MUSICBRAINZ_CONTACT = os.environ.get("MUSICBRAINZ_CONTACT", "")

DISCOGS_BASE_URL = "https://api.discogs.com"
MUSICBRAINZ_BASE_URL = "https://musicbrainz.org/ws/2"


def check_credentials(require_discogs: bool = True) -> None:
    """Exit with a helpful message if required credentials are missing."""
    missing = []
    if not SPOTIFY_CLIENT_ID:
        missing.append("SPOTIFY_CLIENT_ID")
    if not SPOTIFY_CLIENT_SECRET:
        missing.append("SPOTIFY_CLIENT_SECRET")
    if require_discogs and not DISCOGS_TOKEN:
        missing.append("DISCOGS_TOKEN")

    if missing:
        print(
            "Missing required credentials: " + ", ".join(missing) + "\n\n"
            "Set these as environment variables, or create a .env file "
            "(see .env.example) with:\n"
            "  SPOTIFY_CLIENT_ID=...\n"
            "  SPOTIFY_CLIENT_SECRET=...\n"
            "  DISCOGS_TOKEN=...\n",
            file=sys.stderr,
        )
        sys.exit(1)
