"""Mint the one refresh token the whole app runs on.

Context: Google Forms used to be attached by copying a template form into
each admin's own Google Drive, which meant every admin did their own OAuth
against sensitive `drive`/`forms.*` scopes and saw Google's "unverified app"
warning. The replacement design has exactly one Google identity - the club's
own Gmail - own every form; admins are invited as Drive editors on the
specific form instead of authorizing anything themselves. See
docs/GOOGLE_FORMS.md for the full picture.

This script performs that one authorization, interactively, from a laptop
logged into the club's Gmail. It never runs as part of the deployed app and
never writes anything to disk or the database - it only prints the resulting
refresh token, which you paste into Infisical by hand as GOOGLE_REFRESH_TOKEN
under /admin-backend (dev, then prod). Treat that token like any other
production secret: it grants standing access to every form the club account
creates.

One-time setup in Google Cloud Console: this reuses the existing
GOOGLE_CLIENT_ID/SECRET, which - since they were built for the old web OAuth
flow - are a "Web application" client, not a "Desktop app" client. Web clients
only accept pre-registered redirect URIs, so before running this the first
time, open that client under APIs & Services > Credentials and add
http://localhost:8765/ to its Authorized redirect URIs (the port below is
fixed precisely so this only needs registering once).

Run once per environment (or whenever the token is revoked):

    uv run python scripts/setup_google_oauth.py
"""

import sys
from pathlib import Path

script_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(script_dir.parent))

from google_auth_oauthlib.flow import InstalledAppFlow

from app.config import config

SCOPES = [
    # Not drive.file: that scope only sees files this OAuth client created or
    # the user explicitly picked through it, so it can't `files.copy()` a
    # pre-existing template file id at all (404s as if it doesn't exist).
    # Full `drive` is required for the copy step to see the template.
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/forms.body",
    "https://www.googleapis.com/auth/forms.responses.readonly",
]

# Fixed rather than a random free port (the usual InstalledAppFlow default) so
# there is exactly one redirect URI to register on the Web application OAuth
# client this reuses - see the module docstring.
REDIRECT_PORT = 8765


def main():
    client_config = {
        "installed": {
            "client_id": config.GOOGLE_CLIENT_ID,
            "client_secret": config.GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [f"http://localhost:{REDIRECT_PORT}/"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)
    print("Opening a browser window - sign in as the club's Google account, not your own.")
    credentials = flow.run_local_server(port=REDIRECT_PORT, access_type="offline", prompt="consent")

    if not credentials.refresh_token:
        print(
            "\nNo refresh token was returned. This happens when the club account has "
            "already granted this app consent once before - revoke the app's access at "
            "https://myaccount.google.com/permissions and run this script again."
        )
        return

    print("\nSuccess. Paste this into Infisical as GOOGLE_REFRESH_TOKEN under /admin-backend:")
    print(f"\n{credentials.refresh_token}\n")
    print("Do not commit it, email it, or store it anywhere other than Infisical.")


if __name__ == "__main__":
    main()
