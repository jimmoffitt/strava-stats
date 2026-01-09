# run_pipeline.py
import logging
from . import fetch, process
from . import publish

def main():
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    # 1. Fetch
    logger.info("Fetching new activities...")
    raw_data = fetch.get_new_activities() # Saves to data/raw/
    
    # 2. Process
    logger.info("Processing data...")
    df = process.clean_and_aggregate(raw_data) # Saves to data/processed/
    
    # 3. Publish (Generate Images)
    logger.info("Generating static images...")
    publish.generate_footer_stats(df) # Saves to data/images/
    
    logger.info("Pipeline complete!")

if __name__ == "__main__":
    main()