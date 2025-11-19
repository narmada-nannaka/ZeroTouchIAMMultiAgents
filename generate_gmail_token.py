# generate_gmail_token.py
# RUN THIS LOCALLY ON YOUR LAPTOP ONCE to generate token.json

import os.path
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

# If modifying these scopes, delete the file token.json.
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

def main():
    creds = None
    # The file token.json stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first
    # time.
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"Error refreshing token: {e}")
                creds = None

        if not creds:
            # You must have credentials.json from GCP Console (OAuth Client ID -> Desktop App)
            if not os.path.exists("credentials.json"):
                print("❌ ERROR: 'credentials.json' not found.")
                print("1. Go to GCP Console > APIs & Services > Credentials")
                print("2. Create Credentials > OAuth Client ID > Desktop App")
                print("3. Download JSON, rename to 'credentials.json', and place in this folder.")
                return

            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json", SCOPES
            )
            creds = flow.run_local_server(port=0)
        
        # Save the credentials for the next run
        with open("token.json", "w") as token:
            token.write(creds.to_json())
            print("✅ SUCCESS: 'token.json' generated!")
            print("   Include this file in your Cloud Run deployment (root directory).")

if __name__ == "__main__":
    main()