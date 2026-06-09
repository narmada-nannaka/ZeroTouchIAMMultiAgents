"""
One-time OAuth consent helper -- produces the Gmail refresh token the
CommunicationAgent uses to send approval emails as access-bot@narmadanannaka.com.

Run this LOCALLY (not in Cloud Run). It opens a browser; log in as
access-bot@narmadanannaka.com and grant the gmail.send scope. It writes
agbg-anz-zerotouch-senderemail-token.json, which bundles the refresh token plus
the OAuth client id/secret -- everything the deployed agent needs to mint access
tokens. Store that file in Secret Manager; never commit it.

Prereq (local only -- NOT a deployed dependency):
    pip install google-auth-oauthlib

Usage (repo root, with client_secret.json downloaded from the Desktop OAuth client):
    python generate_gmail_token.py
"""
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
CLIENT_SECRET_FILE = "client_secret.json"
TOKEN_FILE = "agbg-anz-zerotouch-senderemail-token.json"


def main():
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
    # prompt='consent' + offline access guarantees a refresh_token is returned,
    # not just a short-lived access token.
    creds = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",
        authorization_prompt_message="Opening browser -- log in as access-bot@narmadanannaka.com and grant Gmail send.",
    )

    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())

    print(f"\nWrote {TOKEN_FILE}")
    print(f"  has refresh_token: {bool(creds.refresh_token)}")
    print(f"  scopes: {creds.scopes}")
    print("Next: store this file in Secret Manager and mount it into the orchestrator.")


if __name__ == "__main__":
    main()
