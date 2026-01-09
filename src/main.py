# src/main.py
import sys
import json
from src import config, fetch_data, process_data, publish_data

def main():
    print("--- Starting Strava Stats Pipeline ---")

    # 1. Validate Config
    try:
        config.validate_config()
    except ValueError as e:
        print(e)
        sys.exit(1)

    # 2. Get Token
    try:
        token = fetch_data.get_access_token(
            token_file=config.TOKEN_FILE,
            client_id=config.CLIENT_ID,
            client_secret=config.CLIENT_SECRET
        )
    except Exception as e:
        print(f"Authentication failed: {e}")
        sys.exit(1)

    # 3. Fetch Gear (Needed for Bike names)
    print("Fetching active gear...")
    gear_map = fetch_data.fetch_active_gear(token)

    # 4. Fetch Activities
    # For a full run, you might want 2024 and 2025
    # For now, let's fetch 2024 (or whichever year you have data for)
    year_to_fetch = 2024 
    print(f"Fetching activities for {year_to_fetch}...")
    activities = fetch_data.fetch_activities_for_year(year_to_fetch, token)
    
    # 5. Process Data
    print("Processing data...")
    df = process_data.process_activities(activities)
    
    # Pass the gear_map to summarize_stats
    summary = process_data.summarize_stats(df, gear_map)
    
    # 6. Publish Data
    print("\nPublishing assets...")
    publish_data.publish_dashboard(summary, config.IMAGES_DIR)

    print("\nPipeline complete.")

if __name__ == "__main__":
    main()