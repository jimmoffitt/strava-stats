import requests
import json
import time
import pandas as pd
import os
import re
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
UNMATCHED_FILE = os.getenv('STRAVA_UNMATCHED_FILE', os.path.join(OUTPUT_DIR, 'unmatched_activities.json'))

# 3. Secrets & Settings
CLIENT_ID = os.getenv('STRAVA_CLIENT_ID')
CLIENT_SECRET = os.getenv('STRAVA_CLIENT_SECRET')

# 4. Script options

years_env = os.getenv('STRAVA_YEARS')
if years_env:
    YEARS_TO_FETCH = [int(year.strip()) for year in years_env.split(',')]
else:
    YEARS_TO_FETCH = [datetime.now().year]

# Maybe there are other things that trigger us to quit. 
if not CLIENT_ID or not CLIENT_SECRET:
    print("❌ ERROR: Credentials not found in .local.env")
    exit(1)

# ==============================================================================
# 1. API INTERACTION LAYER
#    Manage tokens and get activity data.  
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
# 2. ANALYSIS LAYER
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
            
    df['gear_name'] = df['gear_id'].map(gear_map).fillna("Proxy activity")
    df.loc[df['gear_name'] == 'Unknown Name', 'gear_name'] = 'Proxy activity'
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

    df[['display_val', 'display_unit']] = df.apply(
        lambda row: pd.Series(calculate_display_metric(row)), axis=1
    )

    # --- SUMMARY 1: GLOBAL TOTALS ---
    # Calculate Year Range instead of Date Range
    if not df.empty:
        min_year = int(df['year'].min())
        max_year = int(df['year'].max())
        if min_year == max_year:
            range_str = str(min_year)
        else:
            range_str = f"{min_year}-{max_year}"
    else:
        range_str = "N/A"

    global_totals = [
        {'Metric': 'Total Activities', 'Value': str(len(df))},
        {'Metric': 'Year Range', 'Value': range_str}, # <--- Now storing simple Year Range
        {'Metric': 'Active Days', 'Value': str(df['date'].dt.date.nunique())}
    ]

    # --- SUMMARY 2: SPORT RANKING & TOTALS (2025 ONLY) ---
    df_2025 = df[df['year'] == 2025].copy()
    
    if not df_2025.empty:
        sport_stats = df_2025.groupby(['sport_type', 'display_unit']) \
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
    else:
        sport_ranking = []

    # --- SUMMARY 3: LIFETIME BIKE MILEAGE ---
    bike_df = df[df['sport_type'].str.contains('Ride', case=False, na=False)]
    if not bike_df.empty:
        bike_df = bike_df.copy()
        bike_df['miles'] = bike_df['distance'] * 0.000621371
        
        bike_stats = bike_df.groupby('bike_label')['miles'].sum().sort_values(ascending=False).reset_index()
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

    # --- SUMMARY 5: EQUITY ANALYSIS ---
    equity_stats = analyze_equity_linkages(df)

    return {
        "global_stats": global_totals,
        "sport_ranking": sport_ranking,
        "bike_lifetime_miles": bike_lifetime,
        "annual_totals": annual_totals,
        "equity_stats": equity_stats
    }

def analyze_equity_linkages(df):
    target_year = 2025
    print(f"   -> Running Equity (SEq) Analysis for {target_year}...")
    
    df_year = df[df['year'] == target_year].copy()
    if df_year.empty: return {'breakdown': [], 'details': [], 'unmatched': []}

    eq_pattern = re.compile(r'(?:S?Eq)\s*([0-9\.]+)', re.IGNORECASE)
    mask_eq = (df_year['sport_type'] == 'Ride') & (df_year['name'].str.contains(eq_pattern, regex=True))
    eq_rides = df_year[mask_eq].copy()
    
    mask_source = df_year['sport_type'].isin(['Swim', 'AlpineSki', 'Snowboard'])
    source_activities = df_year[mask_source].copy()

    eq_rides['date_str'] = eq_rides['date'].dt.strftime('%Y-%m-%d')
    source_activities['date_str'] = source_activities['date'].dt.strftime('%Y-%m-%d')

    linked_data = []
    unmatched_details = [] 
    
    for _, ride in eq_rides.iterrows():
        match = eq_pattern.search(ride['name'])
        title_val = float(match.group(1)) if match else 0
        
        candidates = source_activities[source_activities['date_str'] == ride['date_str']]
        
        if not candidates.empty:
            src = candidates.iloc[0] 
            source_type = src['sport_type']
            
            if source_type == 'Swim':
                theoretical = src['distance'] / 100.0
                unit = "m"
                src_val = src['distance']
            elif source_type in ['AlpineSki', 'Snowboard']:
                # Ensure we capture Vert Feet
                vert_ft = src['total_elevation_gain'] * 3.28084
                theoretical = vert_ft / 1000.0
                unit = "ft"
                src_val = vert_ft
            else:
                theoretical = 0
                unit = "?"
                src_val = 0

            linked_data.append({
                'date': ride['date_str'],
                'eq_ride_name': ride['name'],
                'eq_miles_title': title_val,
                'source_sport': source_type,
                'source_val': src_val,
                'source_unit': unit,
                'theoretical_eq': theoretical
            })
        else:
            unmatched_details.append({
                'date': ride['date_str'],
                'name': ride['name'],
                'activity_id': ride.get('id', 'Unknown'),
                'miles_recorded': ride['distance'] * 0.000621371,
                'title_value': title_val
            })

    results_df = pd.DataFrame(linked_data)
    
    if results_df.empty: 
        summary_records = []
    else:
        # Group by Sport AND Unit to sum source values correctly
        summary = results_df.groupby(['source_sport', 'source_unit']).agg({
            'eq_miles_title': 'sum',
            'source_val': 'sum'
        }).reset_index()
        
        summary.rename(columns={'eq_miles_title': 'total_miles'}, inplace=True)
        summary_records = summary.to_dict('records')
    
    return {
        'breakdown': summary_records,
        'details': linked_data,
        'unmatched': unmatched_details
    }

# ==============================================================================
# 3. PRESENTATION LAYER
# ==============================================================================

def create_mpl_table(data, columns, filename, footer_text=None, legend_text=None, 
                     legend_loc='top', highlight_last_rows=0, 
                     fig_width=8, save_padding=0.1): # <--- New Params
    """
    Generates a clean table image.
    fig_width: Controls the width of the image (smaller = thinner columns).
    save_padding: Controls whitespace margin around the final PNG.
    """
    if not data: return

    df = pd.DataFrame(data)
    df = df[columns] 
    
    # --- 1. Calculate Dimensions ---
    row_height = 0.5
    header_height = 0.8
    
    # Calculate extra vertical space
    padding = 0.5 
    if legend_text and legend_loc == 'bottom':
        padding += 0.6
        
    fig_height = (len(df) * row_height) + header_height + padding
    
    # Use custom fig_width
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    ax.axis('tight')
    ax.axis('off')
    
    # --- 2. Format Data ---
    cell_text = []
    for row in df.itertuples(index=False):
        formatted_row = []
        for cell in row:
            if isinstance(cell, (int, float)):
                formatted_row.append(f"{cell:,.1f}" if isinstance(cell, float) else f"{cell:,.0f}")
            else:
                formatted_row.append(str(cell))
        cell_text.append(formatted_row)

    # --- 3. Draw Table ---
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

    # --- 4. Row Highlighting ---
    if highlight_last_rows > 0:
        total_rows = len(df)
        start_row = total_rows - highlight_last_rows + 1 
        
        for r in range(start_row, total_rows + 1):
            for c in range(len(columns)):
                cell = table[r, c]
                cell.set_facecolor('#e6e6e6')
                cell.set_text_props(weight='bold') 

    # --- 5. Add Legend ---
    if legend_text:
        if legend_loc == 'bottom':
            # Slightly adjusted y-pos to fit tight spaces
            text_x, text_y = 0.98, 0.08
            va = 'bottom'
        else:
            text_x, text_y = 0.98, 0.95
            va = 'top'

        fig.text(
            text_x, text_y, legend_text, fontsize=9, 
            verticalalignment=va, horizontalalignment='right',
            bbox=dict(boxstyle="round,pad=0.3", facecolor="white", edgecolor="gray", alpha=0.9)
        )

    # --- 6. Add Footer ---
    if footer_text:
        fig.text(0.5, 0.02, footer_text, ha='center', fontsize=8, color='gray')

    save_path = os.path.join(OUTPUT_DIR, filename)
    
    # --- SAVE with Padding ---
    # bbox_inches='tight' crops the whitespace, pad_inches adds it back (creating the margin)
    plt.savefig(save_path, bbox_inches='tight', pad_inches=save_padding, dpi=300)
    
    plt.close(fig)
    print(f"📸 Saved image: {save_path}")

def print_and_save_results(summary):
    
    # 1. Global Stats
    g = summary['global_stats']
    
    # Robustly fetch the Year Range value we just calculated
    year_range_val = next((item['Value'] for item in g if item['Metric'] == 'Year Range'), "Unknown")
    global_footer = f"{year_range_val} Strava activity data"
    
    print("\n=== GLOBAL STATS ===")
    for item in g: print(f"{item['Metric']:<20} : {item['Value']}")
    create_mpl_table(g, ['Metric', 'Value'], '1_global_stats.png', footer_text=global_footer)

    # 2. Sport Stats
    print("\n=== SPORT TOTALS ===")
    s = summary['sport_ranking']
    s_table = [{'Sport': r['sport'], 'Count': r['count'], 'Total': r['total'], 'Unit': r['unit']} for r in s]
    create_mpl_table(s_table, ['Sport', 'Count', 'Total', 'Unit'], '2_sport_stats.png', footer_text="2025 Strava activity data")

    # 3. Bike Stats
    print("\n=== BIKE LIFETIME MILES ===")
    b = summary['bike_lifetime_miles']
    b_table = [{'Bike': r['bike'], 'Miles': r['miles']} for r in b]
    create_mpl_table(b_table, ['Bike', 'Miles'], '3_bike_stats.png', footer_text="Source: Strava activity data for all configured years")

    # 4. Annual Stats
    print("\n=== ANNUAL TOTALS ===")
    a = summary['annual_totals']
    a_table = [{
        'Year': str(r['year']), 
        'Bike (mi)': r['bike_miles'], 
        'Swim (m)': r['swim_meters'], 
        'Ski (ft)': r['ski_vert_ft']
    } for r in a]
    create_mpl_table(a_table, ['Year', 'Bike (mi)', 'Swim (m)', 'Ski (ft)'], '4_annual_stats.png', footer_text=None)

    # 5. Equity Analysis
    print("\n=== EQUIVALENCY (SEq) ANALYSIS ===")
    eq = summary.get('equity_stats', {})
    breakdown = eq.get('breakdown', [])
    
    # Get 2025 actual bike miles from annual_totals
    annual = summary.get('annual_totals', [])
    bike_miles_2025 = next((item['bike_miles'] for item in annual if item['year'] == 2025), 0)
    
    if breakdown or bike_miles_2025 > 0:
        eq_table_data = []
        running_total = 0
        
        # A. Add Proxy Rows
        for row in breakdown:
            eq_table_data.append({
                'Sport': row['source_sport'],
                'Source Dist': f"{row['source_val']:,.0f} {row['source_unit']}",
                'Total Miles': row['total_miles']
            })
            running_total += row['total_miles']
            print(f"{row['source_sport']:<15} {row['total_miles']:<10,.1f}")

        # B. Add Actual Bike Row
        eq_table_data.append({
            'Sport': 'Actual Bike',
            'Source Dist': '-',
            'Total Miles': bike_miles_2025
        })
        running_total += bike_miles_2025

        # C. Add Grand Total Row
        eq_table_data.append({
            'Sport': 'TOTAL',
            'Source Dist': '-',
            'Total Miles': running_total
        })

        legend_txt = (
            "Mileage Equivalents:\n"
            "• Snow sports: 1,000 vert ft = 1 bike mile\n"
            "• Swimming: 100 meters = 1 bike mile"
        )

        create_mpl_table(
            eq_table_data, 
            ['Sport', 'Source Dist', 'Total Miles'], 
            '5_equity_stats.png', 
            footer_text="2025 Strava activity data",
            legend_text=legend_txt,
            legend_loc='bottom',
            highlight_last_rows=2,
            fig_width=6.0,    # Narrower width for thinner columns
            save_padding=0.5  # Extra white margin
        )
    else:
        print("No 'SEq' or 'Eq' activities found.")
        
    # 6. Unmatched Log
    unmatched = eq.get('unmatched', [])
    if unmatched:
        print(f"\n⚠️  Unmatched Activities Logged: {len(unmatched)}")
    else:
        print("\n✅ No unmatched activities found.")
        
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
    
    print("--- Checking for activity data completeness ---")
    for year in YEARS_TO_FETCH:
        year_str = str(year)
        if year == current_year:
            print(f"[{year}] Updating current year...")
            master_db[year_str] = fetch_activities_for_year(year)
        elif year_str not in master_db:
            print(f"[{year}] Missing locally. Downloading...")
            master_db[year_str] = fetch_activities_for_year(year)

    print("Spinning up a data store of activities...")
    with open(ACTIVITIES_FILE, 'w') as f:
        json.dump(master_db, f, indent=2)

    print("--- Checking for activity data completeness ---")
    my_gear_map = fetch_active_gear()
    summaries = analyze_activities(master_db, my_gear_map)

    print("Writing summary files... ")
    with open(SUMMARIES_FILE, 'w') as f:
        json.dump(summaries, f, indent=2)
        
    # --- SAVE UNMATCHED ACTIVITIES ---
    print('Doing custom analysis on tags activities... ')
    unmatched_data = summaries.get('equity_stats', {}).get('unmatched', [])
    with open(UNMATCHED_FILE, 'w') as f:
        json.dump(unmatched_data, f, indent=2)
    if unmatched_data:
        print(f"📄 Unmatched activities saved to: {UNMATCHED_FILE}")

    print_and_save_results(summaries)