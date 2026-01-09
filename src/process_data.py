# src/process_data.py
import pandas as pd

def process_activities(activities_list):
    """
    Converts raw Strava list to a DataFrame and applies custom cleaning logic.
    """
    if not activities_list:
        print("Warning: No activities to process.")
        return pd.DataFrame()

    # 1. Basic conversion
    df = pd.DataFrame(activities_list)
    
    # 2. Date parsing
    df['start_date_local'] = pd.to_datetime(df['start_date_local'])
    df['year'] = df['start_date_local'].dt.year
    
    # 3. Unit Conversion & Defaults
    # Distance: Meters -> Miles (1 meter = 0.000621371 miles)
    df['distance_miles'] = df['distance'] * 0.000621371
    # Elevation: Meters -> Feet (1 meter = 3.28084 feet)
    df['elevation_feet'] = df['total_elevation_gain'] * 3.28084
    
    # Ensure gear_id exists (fill with None if missing)
    if 'gear_id' not in df.columns:
        df['gear_id'] = None

    # 4. Apply Custom Categorization Logic
    df['final_type'] = df.apply(_determine_activity_type, axis=1)
    
    return df

def _determine_activity_type(row):
    """
    Internal helper to handle SBEq, HEq, GEq logic.
    """
    name = str(row.get('name', '')).upper()
    strava_type = row.get('type', 'Unknown')
    
    # Logic: SBEq (Stationary Bike) -> Treat as simple bike miles
    if 'SBEQ' in name: return 'Ride'
    # Logic: HEq -> Hiking
    if 'HEQ' in name: return 'Hiking'
    # Logic: GEq -> Gardening
    if 'GEQ' in name: return 'Gardening'
    
    return strava_type

def summarize_stats(df, gear_map=None):
    """
    Aggregates data into the specific dictionary structure required by the Publish module.
    """
    if df.empty: return {}
    if gear_map is None: gear_map = {}

    summary = {}

    # --- 1. Global Stats ---
    total_miles = df['distance_miles'].sum()
    total_elev = df['elevation_feet'].sum()
    min_year = df['year'].min()
    max_year = df['year'].max()
    
    summary['global_stats'] = [
        {'Metric': 'Total Distance', 'Value': f"{total_miles:,.0f} miles"},
        {'Metric': 'Total Elevation', 'Value': f"{total_elev:,.0f} ft"},
        {'Metric': 'Activities', 'Value': f"{len(df):,}"},
        {'Metric': 'Year Range', 'Value': f"{min_year} - {max_year}"}
    ]

    # --- 2. Sport Ranking ---
    # Group by final_type, sum distance, count items
    sport_stats = df.groupby('final_type').agg(
        count=('id', 'count'),
        total_dist=('distance_miles', 'sum')
    ).reset_index().sort_values('total_dist', ascending=False)
    
    ranking = []
    for _, row in sport_stats.iterrows():
        ranking.append({
            'sport': row['final_type'],
            'count': row['count'],
            'total': f"{row['total_dist']:,.1f}",
            'unit': 'mi'
        })
    summary['sport_ranking'] = ranking

    # --- 3. Bike Lifetime Miles ---
    # Filter for Rides only
    bike_df = df[df['final_type'] == 'Ride']
    # Group by gear_id
    gear_stats = bike_df.groupby('gear_id')['distance_miles'].sum().reset_index()
    
    bike_list = []
    for _, row in gear_stats.iterrows():
        g_id = row['gear_id']
        if g_id: # Ignore None
            bike_name = gear_map.get(g_id, g_id) # Use name if found, else ID
            bike_list.append({
                'bike': bike_name,
                'miles': f"{row['distance_miles']:,.0f}"
            })
    summary['bike_lifetime_miles'] = sorted(bike_list, key=lambda x: float(x['miles'].replace(',','')), reverse=True)

    # --- 4. Annual Totals ---
    # We need specific columns: Bike Miles, Swim Meters, Ski Vert
    annual = []
    years = sorted(df['year'].unique())
    
    for y in years:
        y_df = df[df['year'] == y]
        
        # Calculate specific metrics
        bike_mi = y_df[y_df['final_type'] == 'Ride']['distance_miles'].sum()
        
        # Swim: Sum raw meters (not miles)
        swim_m = y_df[y_df['final_type'] == 'Swim']['distance'].sum()
        
        # Ski: Sum vertical feet (AlpineSki, BackcountrySki, NordicSki, Snowboard)
        # Note: 'final_type' might be just 'AlpineSki' etc. if not caught by custom logic.
        ski_types = ['AlpineSki', 'BackcountrySki', 'NordicSki', 'Snowboard']
        ski_ft = y_df[y_df['final_type'].isin(ski_types)]['elevation_feet'].sum()
        
        annual.append({
            'year': y,
            'bike_miles': int(bike_mi),
            'swim_meters': int(swim_m),
            'ski_vert_ft': int(ski_ft)
        })
    # Sort Descending (Newest first)
    summary['annual_totals'] = sorted(annual, key=lambda x: x['year'], reverse=True)

    # --- 5. Equity Analysis (Calculations) ---
    # Legend says: Swim 100m = 1 mi; Snow 1000ft = 1 mi.
    # We look at the most recent year (or all years? Usually equity is annual).
    # Let's assume we calculate this for the CURRENT (max) year for the table.
    current_year = max_year
    cy_df = df[df['year'] == current_year]
    
    breakdown = []
    
    # Swim Eq
    swim_dist = cy_df[cy_df['final_type'] == 'Swim']['distance'].sum()
    if swim_dist > 0:
        breakdown.append({
            'source_sport': 'Swim',
            'source_val': swim_dist,
            'source_unit': 'm',
            'total_miles': swim_dist / 100.0
        })

    # Snow Eq (Ski types)
    ski_types = ['AlpineSki', 'BackcountrySki', 'NordicSki', 'Snowboard']
    ski_elev = cy_df[cy_df['final_type'].isin(ski_types)]['elevation_feet'].sum()
    if ski_elev > 0:
        breakdown.append({
            'source_sport': 'Snow Sports',
            'source_val': ski_elev,
            'source_unit': 'ft',
            'total_miles': ski_elev / 1000.0
        })

    # Gardening/Hiking (Direct mileage if present)
    # If Hiking/Gardening has distance, we add it 1:1? Or just list it?
    # Assuming 1:1 for now based on "Eq" concept.
    for specialized in ['Hiking', 'Gardening']:
        spec_dist = cy_df[cy_df['final_type'] == specialized]['distance_miles'].sum()
        if spec_dist > 0:
            breakdown.append({
                'source_sport': specialized,
                'source_val': spec_dist,
                'source_unit': 'mi',
                'total_miles': spec_dist
            })

    summary['equity_stats'] = {
        'breakdown': breakdown,
        'unmatched': [] # Placeholder if you want to track unmatched strings later
    }

    return summary