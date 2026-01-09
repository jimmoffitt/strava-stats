import json
import time
import requests
import os
from datetime import datetime

# --- Authentication (Unchanged) ---
def get_access_token(token_file, client_id, client_secret):
    if not os.path.exists(token_file):
        raise FileNotFoundError(f"ERROR: '{token_file}' not found. Please authenticate manually first.")

    with open(token_file, 'r') as f:
        tokens = json.load(f)

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
        tokens.update(response.json())
        with open(token_file, 'w') as f:
            json.dump(tokens, f)
    return tokens['access_token']

def fetch_active_gear(access_token):
    url = "https://www.strava.com/api/v3/athlete"
    response = requests.get(url, headers={'Authorization': f"Bearer {access_token}"})
    if response.status_code != 200: return {}
    data = response.json()
    gear_map = {}
    for bike in data.get('bikes', []): gear_map[bike['id']] = bike['name']
    for shoe in data.get('shoes', []): gear_map[shoe['id']] = shoe['name']
    return gear_map

# --- ARCHIVE MAINTENANCE LOGIC ---

def maintain_archive(access_token, archive_file, target_years):
    """
    Ensures the archive_file contains data for all target_years.
    - If a past year is missing: Fetches it.
    - If a past year is present: Skips it.
    - If the current year is requested: Checks for new data (incremental sync).
    """
    
    # 1. Load Existing Archive
    all_activities = []
    if os.path.exists(archive_file):
        try:
            with open(archive_file, 'r') as f:
                all_activities = json.load(f)
            print(f"Loaded archive: {len(all_activities)} activities found.")
        except json.JSONDecodeError:
            print("⚠️ Archive file was corrupt or empty. Starting fresh.")
            
    # Helper to check if we have data for a specific year
    # We create a set of years present in the data for quick lookup
    present_years = set()
    for act in all_activities:
        # Parse year safely
        start_date = act.get('start_date', '')
        if start_date:
            # ISO format: "2024-01-01T..."
            y = int(start_date[:4])
            present_years.add(y)

    current_year = datetime.now().year
    updated = False

    # 2. Iterate through requested years
    for year in target_years:
        
        # CASE A: Data exists for a PAST year
        if year in present_years and year < current_year:
            print(f"   [OK] {year} data exists in archive. Skipping.")
            continue
            
        # CASE B: Data missing for ANY year (Past or Current)
        if year not in present_years:
            print(f"   [MISSING] {year} data not found. Downloading full year...")
            new_data = _fetch_year(access_token, year)
            if new_data:
                all_activities.extend(new_data)
                present_years.add(year) # Mark as done
                updated = True
            continue
            
        # CASE C: Data exists for CURRENT year (Incremental Update)
        if year == current_year:
            print(f"   [SYNC] Checking for new activities in {year}...")
            # Find the latest timestamp we have for this year
            year_acts = [a for a in all_activities if a['start_date'].startswith(str(year))]
            if not year_acts:
                # Should have been caught by Case B, but safe fallback
                last_ts = datetime(year, 1, 1).timestamp()
            else:
                # Sort to find latest
                year_acts.sort(key=lambda x: x['start_date'])
                last_iso = year_acts[-1]['start_date'].replace('Z', '+00:00')
                last_ts = datetime.fromisoformat(last_iso).timestamp()
            
            # Fetch strictly AFTER that timestamp
            new_data = _fetch_pages(access_token, after_ts=last_ts, before_ts=datetime.now().timestamp())
            
            # Deduplicate (Strava API overlap safety)
            existing_ids = {a['id'] for a in all_activities}
            real_new = [a for a in new_data if a['id'] not in existing_ids]
            
            if real_new:
                print(f"      Found {len(real_new)} new items.")
                all_activities.extend(real_new)
                updated = True
            else:
                print("      Up to date.")

    # 3. Save if changes made
    if updated:
        # Sort entire archive by date before saving
        all_activities.sort(key=lambda x: x.get('start_date', ''))
        
        with open(archive_file, 'w') as f:
            json.dump(all_activities, f, indent=4)
        print(f"✅ Archive updated. Total count: {len(all_activities)}")
    else:
        print("✅ Archive is already up to date.")
        
    # Return the data filtered to ONLY the requested years for processing
    # (The archive might hold 2015, but if we only want 2024-2025, we return those)
    filtered_data = [
        a for a in all_activities 
        if int(a.get('start_date', '')[:4]) in target_years
    ]
    return filtered_data

def _fetch_year(access_token, year):
    dt_start = datetime(year, 1, 1)
    # End of year is Start of next year
    dt_end = datetime(year + 1, 1, 1) 
    return _fetch_pages(access_token, dt_start.timestamp(), dt_end.timestamp())

def _fetch_pages(access_token, after_ts, before_ts):
    activities = []
    page = 1
    while True:
        params = {'per_page': 200, 'page': page, 'after': int(after_ts), 'before': int(before_ts)}
        response = requests.get(
            "https://www.strava.com/api/v3/athlete/activities", 
            headers={'Authorization': f"Bearer {access_token}"}, 
            params=params
        )
        if response.status_code != 200: break
        data = response.json()
        if not data: break
        activities.extend(data)
        page += 1
    return activities