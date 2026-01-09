# run_pipeline.py
import logging
from src import fetch_data, process_data, publish_data

def main():
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    # 1. Fetch
    logger.info("Fetching new activities...")
    raw_data = fetch_data.get_new_activities() # Saves to data/raw/
    
    # 2. Process
    logger.info("Processing data...")
    df = process_data.clean_and_aggregate(raw_data) # Saves to data/processed/
    
    # 3. Publish (Generate Images)
    logger.info("Generating static images...")
    publish_data.generate_footer_stats(df) # Saves to data/images/
    
    logger.info("Pipeline complete!")

if __name__ == "__main__":
    main()