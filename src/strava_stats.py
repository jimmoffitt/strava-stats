import requests
import json
import time
import pandas as pd
import os
import matplotlib.pyplot as plt
from datetime import datetime
from dotenv import load_dotenv

# --- CONFIGURATION ---
load_dotenv(dotenv_path='.local.env')

# 1. Define Directories
DATA_DIR = 'data'
OUTPUT_DIR = 'output'
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 2. Define File Paths
TOKEN_FILE = os.getenv('STRAVA_TOKEN_FILE', os.path.join(DATA_DIR, 'strava_tokens.json'))
ACTIVITIES_FILE = os.getenv('STRAVA_ACTIVITIES_FILE', os.path.join(DATA_DIR, 'my_strava_activities.json'))
SUMMARIES_FILE = os.getenv('STRAVA_SUMMARIES_FILE', os.path.join(OUTPUT_DIR, 'strava_summaries.json'))

# 3. Secrets & Settings
CLIENT_ID = os.getenv('STRAVA_CLIENT_ID')
CLIENT_SECRET = os.getenv('STRAVA_CLIENT_SECRET')

years_env = os.getenv('STRAVA_YEARS')
if years_env:
    YEARS_TO_FETCH = [int(year.strip()) for year in years_env.split(',')]
else:
    YEARS_TO_FETCH = [datetime.now().year]

if not CLIENT_ID or not CLIENT_SECRET:
    print("❌ ERROR: Credentials not found in .local.env")
    exit(1)

# ==============================================================================
# 1. API INTERACTION LAYER (Unchanged)
# ==============================================================================

def get_access_token():
    try:
        with open(TOKEN_FILE, 'r') as f:
            tokens = json.load(f)
    except FileNotFoundError:
        print(f"ERROR: '{TOKEN_FILE}' not found.")
        exit(1)

    if tokens['expires_at'] < time.time() + 300:
        print("Token expired. Refreshing...")
        response = requests.post(
            url='https://www.strava.com/api/v3/oauth/token',
            data={
                'client_id': CLIENT_ID,
                'client_secret': CLIENT_SECRET,
                'grant_type': 'refresh_token',
                'refresh_token': tokens['refresh_token']
            }
        )
        if response.status_code != 200:
            print("Error refreshing token:", response.json())
            exit(1)
        tokens.update(response.json())
        with open(TOKEN_FILE, 'w') as f:
            json.dump(tokens, f)
    return tokens['access_token']

def fetch_active_gear():
    access_token = get_access_token()
    response = requests.get("https://www.strava.com/api/v3/athlete", headers={'Authorization': f"Bearer {access_token}"})
    if response.status_code != 200: return {}
    data = response.json()
    gear_map = {}
    for bike in data.get('bikes', []): gear_map[bike['id']] = bike['name']
    for shoe in data.get('shoes', []): gear_map[shoe['id']] = shoe['name']
    return gear_map

def fetch_single_gear(gear_id):
    access_token = get_access_token()
    response = requests.get(f"https://www.strava.com/api/v3/gear/{gear_id}", headers={'Authorization': f"Bearer {access_token}"})
    return response.json().get('name', 'Unknown Name') if response.status_code == 200 else "Unknown Name"

def fetch_activities_for_year(year):
    access_token = get_access_token()
    activities = []
    page = 1
    dt_start = datetime(year, 1, 1)
    dt_end = datetime(year + 1, 1, 1)
    
    print(f"   -> Downloading {year}...")
    while True:
        params = {'per_page': 200, 'page': page, 'after': int(dt_start.timestamp()), 'before': int(dt_end.timestamp())}
        response = requests.get("https://www.strava.com/api/v3/athlete/activities", headers={'Authorization': f"Bearer {access_token}"}, params=params)
        data = response.json()
        if not data: break
        activities.extend(data)
        print(f"      Page {page} loaded ({len(data)} items)")
        page += 1
    return activities

# ==============================================================================
# 2. ANALYSIS LAYER (Updated logic)
# ==============================================================================

def analyze_activities(master_data, gear_map):
    print("\n--- Starting Analysis ---")
    
    all_activities = []
    for year, activities in master_data.items():
        if activities: all_activities.extend(activities)
            
    if not all_activities: return {}

    df = pd.DataFrame(all_activities)
    
    # Basic Cleaning
    df['date'] = pd.to_datetime(df['start_date_local'])
    df['year'] = df['date'].dt.year
    df['gear_id'] = df['gear_id'].fillna("None")
    
    # Gear Resolution
    unique_ids = df['gear_id'].unique()
    missing_ids = [gid for gid in unique_ids if gid not in gear_map and gid != "None"]
    if missing_ids:
        print(f"Resolving {len(missing_ids)} unknown gear IDs...")
        for gid in missing_ids:
            gear_map[gid] = fetch_single_gear(gid)
            time.sleep(0.2)
    df['gear_name'] = df['gear_id'].map(gear_map).fillna("Unknown")
    df['bike_label'] = df['gear_name']

    # --- LOGIC: Calculate Display Values based on Sport ---
    def calculate_display_metric(row):
        sport = row['sport_type']
        dist_m = row['distance']
        elev_m = row['total_elevation_gain']
        
        if sport in ['AlpineSki', 'Snowboard']:
            return (elev_m * 3.28084), 'ft (vert)'
        elif sport == 'Swim':
            return dist_m, 'm'
        else:
            return (dist_m * 0.000621371), 'mi'

    # Apply logic row by row
    df[['display_val', 'display_unit']] = df.apply(
        lambda row: pd.Series(calculate_display_metric(row)), axis=1
    )

    # --- SUMMARY 1: GLOBAL TOTALS ---
    min_date = df['date'].min().strftime('%Y-%m-%d')
    max_date = df['date'].max().strftime('%Y-%m-%d')
    global_totals = [
        {'Metric': 'Total Activities', 'Value': str(len(df))},
        {'Metric': 'Date Range', 'Value': f"{min_date} to {max_date}"},
        {'Metric': 'Active Days', 'Value': str(df['date'].dt.date.nunique())}
    ]

    # --- SUMMARY 2: SPORT RANKING & TOTALS ---
    # Group by Sport and Unit to sum the display values
    sport_stats = df.groupby(['sport_type', 'display_unit']) \
                    .agg(count=('sport_type', 'count'), total_val=('display_val', 'sum')) \
                    .reset_index() \
                    .sort_values('count', ascending=False)
    
    sport_ranking = []
    for _, row in sport_stats.iterrows():
        sport_ranking.append({
            'sport': row['sport_type'],
            'count': row['count'],
            'total': round(row['total_val']),
            'unit': row['display_unit']
        })

    # --- SUMMARY 3: LIFETIME BIKE MILEAGE ---
    bike_df = df[df['sport_type'].str.contains('Ride', case=False, na=False)]
    if not bike_df.empty:
        bike_df = bike_df.copy()
        bike_df['miles'] = bike_df['distance'] * 0.000621371
        
        # Group and reset index
        bike_stats = bike_df.groupby('bike_label')['miles'].sum().sort_values(ascending=False).reset_index()
        
        # FIX: Rename 'bike_label' to 'bike' so the presentation layer finds it
        bike_stats.rename(columns={'bike_label': 'bike'}, inplace=True)
        
        bike_lifetime = bike_stats.to_dict('records')
    else:
        bike_lifetime = []

    # --- SUMMARY 4: ANNUAL TOTALS ---
    annual_totals = []
    report_years = sorted(YEARS_TO_FETCH, reverse=True)
    
    for year in report_years:
        year_df = df[df['year'] == year]
        
        bike_yr = year_df[year_df['sport_type'].str.contains('Ride', case=False, na=False)]
        swim_yr = year_df[year_df['sport_type'].str.contains('Swim', case=False, na=False)]
        ski_yr = year_df[year_df['sport_type'].str.contains('Ski|Snowboard', regex=True, case=False, na=False)]
        
        annual_totals.append({
            'year': int(year),
            'bike_miles': round(bike_yr['distance'].sum() * 0.000621371 if not bike_yr.empty else 0),
            'swim_meters': int(swim_yr['distance'].sum() if not swim_yr.empty else 0),
            'ski_vert_ft': int(ski_yr['total_elevation_gain'].sum() * 3.28084 if not ski_yr.empty else 0)
        })

    return {
        "global_stats": global_totals,
        "sport_ranking": sport_ranking,
        "bike_lifetime_miles": bike_lifetime,
        "annual_totals": annual_totals
    }

# ==============================================================================
# 3. PRESENTATION LAYER (Updated for multiple PNGs)
# ==============================================================================

def create_mpl_table(data, columns, filename):
    """Helper to generate a clean table image from a list of dicts"""
    if not data: return

    df = pd.DataFrame(data)
    # Filter only requested columns
    df = df[columns] 
    
    # Calculate figure height dynamically
    row_height = 0.5
    header_height = 0.8
    fig_height = (len(df) * row_height) + header_height
    
    fig, ax = plt.subplots(figsize=(8, fig_height))
    ax.axis('tight')
    ax.axis('off')
    
    # Format data for table (add commas for numbers)
    cell_text = []
    for row in df.itertuples(index=False):
        formatted_row = []
        for cell in row:
            if isinstance(cell, (int, float)):
                formatted_row.append(f"{cell:,.0f}")
            else:
                formatted_row.append(str(cell))
        cell_text.append(formatted_row)

    table = ax.table(
        cellText=cell_text, 
        colLabels=columns, 
        loc='center', 
        cellLoc='center',
        colColours=['#e6e6e6'] * len(columns)
    )
    
    table.auto_set_font_size(False)
    table.set_fontsize(12)
    table.scale(1.2, 1.8)
    
    save_path = os.path.join(OUTPUT_DIR, filename)
    plt.savefig(save_path, bbox_inches='tight', dpi=300)
    plt.close(fig)
    print(f"📸 Saved image: {save_path}")

def print_and_save_results(summary):
    # 1. Global Stats
    print("\n=== GLOBAL STATS ===")
    g = summary['global_stats']
    for item in g:
        print(f"{item['Metric']:<20} : {item['Value']}")
    create_mpl_table(g, ['Metric', 'Value'], '1_global_stats.png')

    # 2. Sports
    print("\n=== SPORT TOTALS ===")
    s = summary['sport_ranking']
    print(f"{'SPORT':<20} {'COUNT':<8} {'TOTAL':<10} {'UNIT'}")
    print("-" * 50)
    for row in s:
        print(f"{row['sport']:<20} {row['count']:<8} {row['total']:<10,.0f} {row['unit']}")
    
    # Flatten dict for table (combine total/unit)
    s_table = []
    for row in s:
        s_table.append({
            'Sport': row['sport'], 
            'Count': row['count'], 
            'Total': row['total'], 
            'Unit': row['unit']
        })
    create_mpl_table(s_table, ['Sport', 'Count', 'Total', 'Unit'], '2_sport_stats.png')

    # 3. Bikes
    print("\n=== BIKE LIFETIME MILES ===")
    b = summary['bike_lifetime_miles']
    for row in b:
        print(f"{row['bike']:<30} : {row['miles']:,.0f} mi")
    
    b_table = [{'Bike': r['bike'], 'Miles': r['miles']} for r in b]
    create_mpl_table(b_table, ['Bike', 'Miles'], '3_bike_stats.png')

    # 4. Annual
    print("\n=== ANNUAL TOTALS ===")
    a = summary['annual_totals']
    print(f"{'YEAR':<6} {'BIKE(mi)':<10} {'SWIM(m)':<10} {'SKI(ft)':<10}")
    for row in a:
        print(f"{row['year']:<6} {row['bike_miles']:<10,.0f} {row['swim_meters']:<10,.0f} {row['ski_vert_ft']:<10,.0f}")
    
    a_table = [{'Year': r['year'], 'Bike (mi)': r['bike_miles'], 'Swim (m)': r['swim_meters'], 'Ski (ft)': r['ski_vert_ft']} for r in a]
    create_mpl_table(a_table, ['Year', 'Bike (mi)', 'Swim (m)', 'Ski (ft)'], '4_annual_stats.png')

# ==============================================================================
# 4. MAIN ORCHESTRATION
# ==============================================================================

if __name__ == "__main__":
    if os.path.exists(ACTIVITIES_FILE):
        with open(ACTIVITIES_FILE, 'r') as f:
            master_db = json.load(f)
    else:
        master_db = {}

    current_year = datetime.now().year
    
    print("--- Checking for updates ---")
    for year in YEARS_TO_FETCH:
        year_str = str(year)
        if year == current_year:
            print(f"[{year}] Updating current year...")
            master_db[year_str] = fetch_activities_for_year(year)
        elif year_str not in master_db:
            print(f"[{year}] Missing locally. Downloading...")
            master_db[year_str] = fetch_activities_for_year(year)

    with open(ACTIVITIES_FILE, 'w') as f:
        json.dump(master_db, f, indent=2)

    my_gear_map = fetch_active_gear()
    summaries = analyze_activities(master_db, my_gear_map)

    with open(SUMMARIES_FILE, 'w') as f:
        json.dump(summaries, f, indent=2)

    print_and_save_results(summaries)