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

# 5. Sport/Gear Constants
BIKE_TYPES = ['Ride', 'VirtualRide', 'EBikeRide']
SKI_TYPES = ['AlpineSki', 'BackcountrySki', 'NordicSki', 'Snowboard']

# Historical gear that may be retired from Strava API but still in archive
GEAR_FALLBACKS = {
    'b6971509': 'Gravel bike (DB Haanjo Comp)',
    'b11542587': 'Ferrazi Delano Peak Comp',
}

GEAR_MAP_FILE      = os.path.join(DATA_DIR, 'gear_map.json')
SETTINGS_FILE      = os.path.join(DATA_DIR, 'settings.json')
ATHLETE_PROFILE_FILE = os.path.join(DATA_DIR, 'athlete_profile.json')
ATHLETE_STATS_FILE   = os.path.join(DATA_DIR, 'athlete_stats.json')

# Defaults used when settings.json doesn't exist yet
DEFAULT_SETTINGS = {
    'conversions': {
        'swim_meters_per_mile': 100,   # 100 m swim = 1 equity mile
        'ski_vert_per_mile': 1000,     # 1,000 vert ft = 1 equity mile
    },
    'goals': {
        'annual_equity_miles': 3000,
        'monthly_equity_miles': 250,
        'ski_season_vert_ft': 200000,  # cumulative season total
        'swim_monthly_meters': 10000,  # per-month target
    },
}

def validate_config():
    if not CLIENT_ID or not CLIENT_SECRET:
        raise ValueError("❌ ERROR: Credentials not found in .local.env")
    if not STRAVA_YEARS:
        raise ValueError("❌ ERROR: No valid years found in STRAVA_YEARS setting.")