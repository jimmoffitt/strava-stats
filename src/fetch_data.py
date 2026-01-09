# src/fetch_data.py
import json
import time
import requests
import os
from datetime import datetime

# --- Authentication ---

def get_access_token(token_file, client_id, client_secret):
    """
    Reads the token file, refreshes if necessary, and returns a valid access token.
    """
    if not os.path.exists(token_file):
        raise FileNotFoundError(f"ERROR: '{token_file}' not found. Please authenticate manually first.")

    with open(token_file, 'r') as f:
        tokens = json.load(f)

    # Check if expired (with a 5-minute buffer)
    if tokens['expires_at'] < time.time() + 300:
        print("Token expired. Refreshing...")
        response = requests.post(
            url='https://www.strava.com/api/v3/oauth/token',
            data={
                'client_id': client_id,
                'client_secret': client_secret,
                'grant_type': 'refresh_token',
                'refresh_token': tokens['refresh_token']
            }
        )
        
        if response.status_code != 200:
            raise ConnectionError(f"Error refreshing token: {response.json()}")
            
        new_tokens = response.json()
        tokens.update(new_tokens)
        
        with open(token_file, 'w') as f:
            json.dump(tokens, f)
            
    return tokens['access_token']


# --- Data Fetching ---

def fetch_active_gear(access_token):
    """
    Fetches the athlete's currently active gear (shoes/bikes) and returns a map of ID -> Name.
    """
    url = "https://www.strava.com/api/v3/athlete"
    response = requests.get(url, headers={'Authorization': f"Bearer {access_token}"})
    
    if response.status_code != 200:
        print(f"Warning: Could not fetch athlete profile (Status {response.status_code})")
        return {}
        
    data = response.json()
    gear_map = {}
    
    for bike in data.get('bikes', []): 
        gear_map[bike['id']] = bike['name']
    for shoe in data.get('shoes', []): 
        gear_map[shoe['id']] = shoe['name']
        
    return gear_map

def fetch_single_gear(gear_id, access_token):
    """
    Fetches details for a specific gear ID if not found in the active list.
    """
    url = f"https://www.strava.com/api/v3/gear/{gear_id}"
    response = requests.get(url, headers={'Authorization': f"Bearer {access_token}"})
    
    if response.status_code == 200:
        return response.json().get('name', 'Unknown Name')
    return "Unknown Name"

def fetch_activities_for_year(year, access_token):
    """
    Paginated fetch of all activities for a specific year.
    """
    activities = []
    page = 1
    dt_start = datetime(year, 1, 1)
    dt_end = datetime(year + 1, 1, 1)
    
    print(f"   -> Downloading {year}...")
    
    while True:
        params = {
            'per_page': 200, 
            'page': page, 
            'after': int(dt_start.timestamp()), 
            'before': int(dt_end.timestamp())
        }
        
        response = requests.get(
            "https://www.strava.com/api/v3/athlete/activities", 
            headers={'Authorization': f"Bearer {access_token}"}, 
            params=params
        )
        
        # Handle API errors gracefully
        if response.status_code != 200:
            print(f"      Error on page {page}: {response.status_code}")
            break

        data = response.json()
        if not data: 
            break
            
        activities.extend(data)
        print(f"      Page {page} loaded ({len(data)} items)")
        page += 1
        
    return activities