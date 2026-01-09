import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv(dotenv_path='.local.env')

# 1. Define Directories
DATA_DIR = 'data'
PROCESSED_DIR = os.path.join(DATA_DIR, 'processed') 
IMAGES_DIR = os.path.join(DATA_DIR, 'images')
RAW_DIR = os.path.join(DATA_DIR, 'raw')

for d in [DATA_DIR, PROCESSED_DIR, IMAGES_DIR, RAW_DIR]:
    os.makedirs(d, exist_ok=True)

# 2. Define File Paths
TOKEN_FILE = os.getenv('STRAVA_TOKEN_FILE', os.path.join(DATA_DIR, 'strava_tokens.json'))

# SINGLE ARCHIVE FILE
ACTIVITIES_FILE = os.path.join(RAW_DIR, 'my_strava_activities.json') 

# 3. Secrets
CLIENT_ID = os.getenv('STRAVA_CLIENT_ID')
CLIENT_SECRET = os.getenv('STRAVA_CLIENT_SECRET')

# 4. Target Years Configuration
# Default to current year if not set
default_years = str(os.getenv('STRAVA_YEARS', '2025'))
# Convert comma-string "2023,2024" -> List [2023, 2024]
STRAVA_YEARS = [int(y.strip()) for y in default_years.split(',') if y.strip().isdigit()]

def validate_config():
    if not CLIENT_ID or not CLIENT_SECRET:
        raise ValueError("❌ ERROR: Credentials not found in .local.env")
    if not STRAVA_YEARS:
        raise ValueError("❌ ERROR: No valid years found in STRAVA_YEARS setting.")