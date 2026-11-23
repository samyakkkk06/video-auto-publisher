"""One time helper to get a YouTube refresh token (official OAuth flow).

Run:  python -m scripts.youtube_auth

It opens your browser, you click Allow, and the refresh token is printed
and written to .env for you. Free Google Cloud project + free YouTube API.
"""
from __future__ import annotations

import sys
from pathlib import Path

from .config import ROOT, load_config

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube"]


def main() -> int:
    cfg = load_config()
    client_id = cfg.env_get("YOUTUBE_CLIENT_ID")
    client_secret = cfg.env_get("YOUTUBE_CLIENT_SECRET")

    if not (client_id and client_secret):
        print("Set YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET in .env first.")
        return 1

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("Missing dependency. Run: pip install -r requirements.txt")
        return 1

    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        },
        scopes=SCOPES,
    )

    print("\nOpening your browser for Google sign in...")
    print("Choose the Google account that owns the YouTube channel.\n")
    credentials = flow.run_local_server(port=8765, open_browser=True, prompt_authorization_code=False)

    token = credentials.refresh_token
    if not token:
        print("No refresh token returned. Revoke access and run again.")
        return 1

    env_path = ROOT / ".env"
    lines = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    replaced = False
    for index, line in enumerate(lines):
        if line.strip().startswith("YOUTUBE_REFRESH_TOKEN="):
            lines[index] = f"YOUTUBE_REFRESH_TOKEN={token}"
            replaced = True
    if not replaced:
        lines.append(f"YOUTUBE_REFRESH_TOKEN={token}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\nRefresh token saved to .env")
    print("YouTube is connected. Nothing else to do for YouTube.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
