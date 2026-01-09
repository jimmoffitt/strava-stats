# src/config.py
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv(dotenv_path='.local.env')

# 1. Define Directories
DATA_DIR = 'data'
# Note: You mentioned 'processed' and 'images' in previous turns, 
# so we can standardize them here if you like, or stick to your current 'output'
PROCESSED_DIR = os.path.join(DATA_DIR, 'processed') 
IMAGES_DIR = os.path.join(DATA_DIR, 'images')
RAW_DIR = os.path.join(DATA_DIR, 'raw')

# Ensure they exist
for d in [DATA_DIR, PROCESSED_DIR, IMAGES_DIR, RAW_DIR]:
    os.makedirs(d, exist_ok=True)

# 2. Define File Paths
TOKEN_FILE = os.getenv('STRAVA_TOKEN_FILE', os.path.join(DATA_DIR, 'strava_tokens.json'))
# Using raw folder for the initial fetch
ACTIVITIES_FILE = os.path.join(RAW_DIR, 'my_strava_activities.json') 

# 3. Secrets
CLIENT_ID = os.getenv('STRAVA_CLIENT_ID')
CLIENT_SECRET = os.getenv('STRAVA_CLIENT_SECRET')

def validate_config():
    """Fail early if secrets are missing."""
    if not CLIENT_ID or not CLIENT_SECRET:
        raise ValueError("❌ ERROR: Credentials not found in .local.env")