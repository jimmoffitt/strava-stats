# src/process_data.py
import pandas as pd
from datetime import date, timedelta

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

def aggregate_by_year(bike_df):
    """
    Returns a DataFrame with one row per year: year, miles, km, hours, count.
    """
    bike_df = bike_df.copy()
    bike_df['km'] = bike_df['distance'] / 1000.0
    bike_df['hours'] = bike_df['moving_time'] / 3600.0
    agg = (
        bike_df.groupby('year')
        .agg(
            miles=('distance_miles', 'sum'),
            km=('km', 'sum'),
            hours=('hours', 'sum'),
            count=('id', 'count'),
        )
        .reset_index()
        .sort_values('year')
    )
    return agg


def aggregate_by_month(bike_df, year, month):
    """
    Returns a DataFrame with one row per day-of-month for the given year/month.
    Missing days are filled with 0s.
    """
    import calendar
    bike_df = bike_df.copy()
    bike_df['km'] = bike_df['distance'] / 1000.0
    bike_df['hours'] = bike_df['moving_time'] / 3600.0
    bike_df['day'] = bike_df['start_date_local'].dt.day

    mask = (bike_df['start_date_local'].dt.year == year) & (bike_df['start_date_local'].dt.month == month)
    filtered = bike_df[mask]

    agg = (
        filtered.groupby('day')
        .agg(
            miles=('distance_miles', 'sum'),
            km=('km', 'sum'),
            hours=('hours', 'sum'),
            count=('id', 'count'),
        )
        .reset_index()
    )

    # Left-join to all days in the month so missing days show 0
    num_days = calendar.monthrange(year, month)[1]
    all_days = pd.DataFrame({'day': range(1, num_days + 1)})
    result = all_days.merge(agg, on='day', how='left').fillna(0)
    result['day'] = result['day'].astype(int)
    return result


def aggregate_by_iso_week(bike_df, iso_year, iso_week):
    """
    Returns a DataFrame with one row per weekday (Mon–Sun) for the given ISO week.
    Missing days are filled with 0s.
    """
    bike_df = bike_df.copy()
    bike_df['km'] = bike_df['distance'] / 1000.0
    bike_df['hours'] = bike_df['moving_time'] / 3600.0

    # Compute Monday of the target ISO week
    monday = date.fromisocalendar(iso_year, iso_week, 1)
    sunday = monday + timedelta(days=6)

    # Use .dt.date for comparison (start_date_local may be tz-aware)
    dates = bike_df['start_date_local'].dt.date
    mask = (dates >= monday) & (dates <= sunday)
    filtered = bike_df[mask].copy()

    # Weekday: 0=Mon, 6=Sun
    filtered['weekday'] = filtered['start_date_local'].dt.weekday

    agg = (
        filtered.groupby('weekday')
        .agg(
            miles=('distance_miles', 'sum'),
            km=('km', 'sum'),
            hours=('hours', 'sum'),
            count=('id', 'count'),
        )
        .reset_index()
    )

    day_labels = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    all_days = pd.DataFrame({'weekday': range(7), 'day_label': day_labels})
    result = all_days.merge(agg, on='weekday', how='left').fillna(0)
    result['weekday'] = result['weekday'].astype(int)
    return result


def get_period_stats(bike_df, year, month=None, iso_week=None):
    """
    Returns scalar summary dict for a period: {miles, km, hours, count}.
    Pass month for month-mode, iso_week for week-mode.
    """
    bike_df = bike_df.copy()
    bike_df['km'] = bike_df['distance'] / 1000.0
    bike_df['hours'] = bike_df['moving_time'] / 3600.0

    if iso_week is not None:
        monday = date.fromisocalendar(year, iso_week, 1)
        sunday = monday + timedelta(days=6)
        dates = bike_df['start_date_local'].dt.date
        mask = (dates >= monday) & (dates <= sunday)
    elif month is not None:
        mask = (bike_df['start_date_local'].dt.year == year) & (bike_df['start_date_local'].dt.month == month)
    else:
        mask = bike_df['start_date_local'].dt.year == year

    filtered = bike_df[mask]
    return {
        'miles': filtered['distance_miles'].sum(),
        'km': filtered['km'].sum(),
        'hours': filtered['hours'].sum(),
        'count': len(filtered),
    }


def compute_wrapped_stats(df, year):
    """
    Returns a comprehensive dict of Wrapped-style stats for the given year.
    All stats are derived from the processed activity DataFrame.
    """
    import calendar as cal

    y_df = df[df['year'] == year].copy()
    p_df = df[df['year'] == year - 1].copy()

    if y_df.empty:
        return {}

    def _totals(d):
        return {
            'activities': len(d),
            'miles':      d['distance_miles'].sum(),
            'km':         (d['distance'] / 1000).sum(),
            'hours':      d['moving_time'].sum() / 3600,
            'vert_ft':    d['elevation_feet'].sum(),
        }

    curr = _totals(y_df)
    prev = _totals(p_df)

    # --- Monthly breakdown (all 12 months, fill 0s) ---
    y_df['month'] = y_df['start_date_local'].dt.month
    all_months = pd.DataFrame({
        'month': range(1, 13),
        'month_name': [cal.month_abbr[m] for m in range(1, 13)],
    })
    monthly_agg = (
        y_df.groupby('month')
        .agg(
            activities=('id', 'count'),
            miles=('distance_miles', 'sum'),
            km=('distance', lambda x: x.sum() / 1000),
            hours=('moving_time', lambda x: x.sum() / 3600),
        )
        .reset_index()
    )
    monthly = all_months.merge(monthly_agg, on='month', how='left').fillna(0)
    monthly['month'] = monthly['month'].astype(int)

    # --- Sport breakdown ---
    sport = (
        y_df.groupby('final_type')
        .agg(
            activities=('id', 'count'),
            miles=('distance_miles', 'sum'),
            km=('distance', lambda x: x.sum() / 1000),
            hours=('moving_time', lambda x: x.sum() / 3600),
            vert_ft=('elevation_feet', 'sum'),
        )
        .reset_index()
        .sort_values('activities', ascending=False)
    )

    # --- Biggest week ---
    iso = y_df['start_date_local'].dt.isocalendar()
    y_df['iso_year'] = iso['year'].values
    y_df['iso_week'] = iso['week'].values
    weekly = y_df.groupby(['iso_year', 'iso_week'])['distance_miles'].sum()
    if not weekly.empty:
        bw_idx = weekly.idxmax()
        biggest_week = {'miles': weekly.max(), 'label': f"Week {bw_idx[1]}, {bw_idx[0]}"}
    else:
        biggest_week = {'miles': 0, 'label': 'N/A'}

    # --- Longest single activity ---
    la_row = y_df.loc[y_df['distance_miles'].idxmax()]
    longest_activity = {
        'name':  la_row['name'],
        'date':  la_row['start_date_local'].date(),
        'miles': la_row['distance_miles'],
        'type':  la_row['final_type'],
    }

    # --- Most elevation in a single day ---
    y_df['date'] = y_df['start_date_local'].dt.date
    daily_vert = y_df.groupby('date')['elevation_feet'].sum()
    bv_date = daily_vert.idxmax()
    best_vert_day = {'date': bv_date, 'vert_ft': daily_vert.max()}

    # --- Social ---
    total_kudos = int(y_df['kudos_count'].sum()) if 'kudos_count' in y_df.columns else 0
    most_kudoed = None
    if 'kudos_count' in y_df.columns and not y_df.empty:
        mk_row = y_df.loc[y_df['kudos_count'].idxmax()]
        most_kudoed = {
            'name':  mk_row['name'],
            'date':  mk_row['start_date_local'].date(),
            'kudos': int(mk_row['kudos_count']),
        }
    group_rides = int((y_df['athlete_count'] > 1).sum()) if 'athlete_count' in y_df.columns else 0

    # --- Achievements ---
    total_prs          = int(y_df['pr_count'].sum())          if 'pr_count'          in y_df.columns else 0
    total_achievements = int(y_df['achievement_count'].sum()) if 'achievement_count' in y_df.columns else 0

    # --- Longest streak (consecutive active days) ---
    dates = sorted(y_df['date'].unique())
    max_streak = cur_streak = (1 if dates else 0)
    for i in range(1, len(dates)):
        if (dates[i] - dates[i - 1]).days == 1:
            cur_streak += 1
            max_streak = max(max_streak, cur_streak)
        else:
            cur_streak = 1

    # --- Fun facts ---
    fun_facts = {
        'everests':   curr['vert_ft'] / 29032,   # Everest = 29,032 ft
        'earth_pct':  curr['miles'] / 24901 * 100,  # Earth circumference
        'days_moving': curr['hours'] / 24,
    }

    return {
        'year':            year,
        'totals':          curr,
        'prior_totals':    prev,
        'monthly':         monthly,
        'sport_breakdown': sport,
        'biggest_week':    biggest_week,
        'longest_activity': longest_activity,
        'best_vert_day':   best_vert_day,
        'kudos':           {'total': total_kudos, 'most_kudoed': most_kudoed},
        'group_rides':     group_rides,
        'achievements':    {'prs': total_prs, 'total': total_achievements},
        'longest_streak':  max_streak,
        'fun_facts':       fun_facts,
    }


def _ski_season_key(dt):
    """Returns the start year of the ski season for a datetime. Oct-Dec → same year; Jan-Sep → prior year."""
    return dt.year if dt.month >= 10 else dt.year - 1


def aggregate_ski_by_season(ski_df):
    """
    Returns a DataFrame with one row per ski season.
    Columns: season_key, season_label, days, sessions, vert_ft, distance_miles, hours.
    """
    ski_df = ski_df.copy()
    ski_df['season_key'] = ski_df['start_date_local'].apply(_ski_season_key)
    ski_df['date'] = ski_df['start_date_local'].dt.date
    ski_df['hours'] = ski_df['moving_time'] / 3600.0

    agg = (
        ski_df.groupby('season_key')
        .agg(
            days=('date', 'nunique'),
            sessions=('id', 'count'),
            vert_ft=('elevation_feet', 'sum'),
            distance_miles=('distance_miles', 'sum'),
            hours=('hours', 'sum'),
        )
        .reset_index()
        .sort_values('season_key')
    )
    agg['season_label'] = agg['season_key'].apply(lambda y: f"{y}-{str(y + 1)[-2:]}")
    return agg


def get_ski_days_table(ski_df, season_key):
    """
    Returns a DataFrame with one row per calendar day in the given season.
    Multiple sessions on the same day are aggregated; activity names are joined.
    """
    ski_df = ski_df.copy()
    ski_df['season_key'] = ski_df['start_date_local'].apply(_ski_season_key)
    ski_df['date'] = ski_df['start_date_local'].dt.date
    ski_df['hours'] = ski_df['moving_time'] / 3600.0

    season = ski_df[ski_df['season_key'] == season_key].copy()
    if season.empty:
        return pd.DataFrame(columns=['date', 'activity', 'type', 'vert_ft', 'distance_mi', 'hours'])

    # Aggregate numeric cols by date
    numeric = (
        season.groupby('date')
        .agg(
            vert_ft=('elevation_feet', 'sum'),
            distance_mi=('distance_miles', 'sum'),
            hours=('hours', 'sum'),
            sessions=('id', 'count'),
        )
        .reset_index()
    )

    # Join activity names and pick dominant type per day
    names = season.groupby('date')['name'].apply(' + '.join).reset_index().rename(columns={'name': 'activity'})
    types = season.groupby('date')['final_type'].first().reset_index().rename(columns={'final_type': 'type'})

    result = numeric.merge(names, on='date').merge(types, on='date')
    return result.sort_values('date', ascending=False).reset_index(drop=True)


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
            bike_name = bike_name.encode('ascii', 'ignore').decode('ascii').strip()
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