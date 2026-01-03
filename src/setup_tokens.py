import requests
import json
import os
from dotenv import load_dotenv

# Load from .local.env if available, otherwise .env
load_dotenv(dotenv_path='.local.env')

# --- CONFIGURATION ---
# It tries to load from env, but you can hardcode here if running for the first time without env
client_id = os.getenv('STRAVA_CLIENT_ID', 'YOUR_CLIENT_ID_HERE')
client_secret = os.getenv('STRAVA_CLIENT_SECRET', 'YOUR_CLIENT_SECRET_HERE')

# Ensure DATA directory exists
DATA_DIR = 'data'
os.makedirs(DATA_DIR, exist_ok=True)
TOKEN_FILE = os.path.join(DATA_DIR, 'strava_tokens.json')

# 1. GENERATE THE AUTHORIZATION URL
print(f"--- Strava Auth Setup ---")
print(f"Target File: {TOKEN_FILE}\n")
print("1. Go to the following URL in your browser to authorize:")

auth_url = (
    f"https://www.strava.com/oauth/authorize?"
    f"client_id={client_id}&response_type=code&"
    f"redirect_uri=http://localhost/exchange_token&"
    f"approval_prompt=force&scope=activity:read_all,profile:read_all"
)
print(auth_url)

# 2. USER INPUT
print("\n2. After you click 'Authorize', you will be redirected to a localhost page that fails.")
print("   Copy the 'code' from the URL (everything after code= and before &scope)")
auth_code = input("\n   Paste the 'code' here: ")

# 3. EXCHANGE CODE FOR TOKENS
print("\n3. Exchanging code for tokens...")
token_url = "https://www.strava.com/oauth/token"
payload = {
    'client_id': client_id,
    'client_secret': client_secret,
    'code': auth_code,
    'grant_type': 'authorization_code'
}

response = requests.post(token_url, data=payload)

if response.status_code == 200:
    tokens = response.json()
    with open(TOKEN_FILE, 'w') as f:
        json.dump(tokens, f)
    print(f"\nSUCCESS! Tokens saved to '{TOKEN_FILE}'")
else:
    print("\nError exchanging token:")
    print(response.json())