import sys
from src import config, fetch_data, process_data, publish_data

def main():
    print("--- Starting Strava Stats Pipeline ---")
    print(f"Target Years: {config.STRAVA_YEARS}")

    # 1. Config & Auth
    try:
        config.validate_config()
        token = fetch_data.get_access_token(config.TOKEN_FILE, config.CLIENT_ID, config.CLIENT_SECRET)
        gear_map = fetch_data.fetch_active_gear(token)
    except Exception as e:
        print(f"Setup failed: {e}")
        sys.exit(1)

    # 2. Maintain Archive & Get Relevant Data
    # This will update 'my_strava_activities.json' if needed, 
    # and return ONLY the activities matching STRAVA_YEARS
    activities = fetch_data.maintain_archive(
        access_token=token, 
        archive_file=config.ACTIVITIES_FILE, 
        target_years=config.STRAVA_YEARS
    )

    # 3. Process & Publish
    if activities:
        print(f"Processing {len(activities)} activities...")
        df = process_data.process_activities(activities)
        summary = process_data.summarize_stats(df, gear_map)
        
        print("Publishing assets...")
        publish_data.publish_dashboard(summary, df, config.IMAGES_DIR)
    else:
        print("No activities found for the requested years.")

    print("\nPipeline complete.")

if __name__ == "__main__":
    main()