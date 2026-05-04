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
    
    # 2. Date parsing — normalize to tz-naive so all comparisons are consistent.
    # Strava stores start_date_local with a Z suffix (UTC-equivalent) even though
    # the value represents local time. Stripping tz info here means every caller
    # can compare dates without tz-aware/tz-naive mismatches.
    df['start_date_local'] = (
        pd.to_datetime(df['start_date_local'], utc=True).dt.tz_convert(None)
    )
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
    Internal helper to handle SBEq, HEq, GEq, SEq logic.
    """
    name = str(row.get('name', '')).upper()
    strava_type = row.get('type', 'Unknown')

    if 'SBEQ' in name: return 'Ride'
    if 'HEQ' in name: return 'Hiking'
    if 'GEQ' in name: return 'Gardening'

    if 'SEQ' in name:
        # SEq has been used for both swimming and skiing activities.
        # Date rule: May 7 – Oct 31 → Swim; Nov 1 – May 6 → AlpineSki.
        dt = row.get('start_date_local')
        if dt is not None:
            ts = pd.Timestamp(dt)
            if (ts.month, ts.day) >= (5, 7) and (ts.month, ts.day) < (11, 1):
                return 'Swim'
            return 'AlpineSki'

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


def aggregate_swim_by_year(swim_df):
    """Returns DataFrame[year, swims, meters, yards, hours, avg_meters] sorted ascending."""
    swim_df = swim_df.copy()
    swim_df['hours'] = swim_df['moving_time'] / 3600.0
    agg = (
        swim_df.groupby('year')
        .agg(
            swims=('id', 'count'),
            meters=('distance', 'sum'),
            hours=('hours', 'sum'),
        )
        .reset_index()
        .sort_values('year')
    )
    agg['yards']      = agg['meters'] * 1.09361
    agg['avg_meters'] = agg['meters'] / agg['swims']
    return agg


def aggregate_swim_by_month(swim_df, year):
    """
    Returns DataFrame with one row per month (1–12) for the given year.
    Columns: month, month_name, swims, meters, yards, hours.
    Missing months filled with 0.
    """
    import calendar as cal
    swim_df = swim_df.copy()
    swim_df['hours'] = swim_df['moving_time'] / 3600.0
    swim_df['month'] = swim_df['start_date_local'].dt.month

    mask = swim_df['start_date_local'].dt.year == year
    agg = (
        swim_df[mask].groupby('month')
        .agg(
            swims=('id', 'count'),
            meters=('distance', 'sum'),
            hours=('hours', 'sum'),
        )
        .reset_index()
    )
    all_months = pd.DataFrame({
        'month':      range(1, 13),
        'month_name': [cal.month_abbr[m] for m in range(1, 13)],
    })
    result = all_months.merge(agg, on='month', how='left').fillna(0)
    result['month'] = result['month'].astype(int)
    result['yards'] = result['meters'] * 1.09361
    return result


def get_swim_log(swim_df, year):
    """
    Returns a DataFrame of individual swims for the given year, sorted newest first.
    Includes pace in seconds per 100m (and per 100yd).
    """
    swim_df = swim_df.copy()
    year_df = swim_df[swim_df['year'] == year].copy()
    if year_df.empty:
        return pd.DataFrame()

    year_df['yards'] = year_df['distance'] * 1.09361
    year_df['hours'] = year_df['moving_time'] / 3600.0

    def _pace_sec(row):
        spd = row.get('average_speed', 0)
        if spd and spd > 0:
            return 100.0 / spd      # seconds per 100m
        elif row['distance'] > 0 and row['moving_time'] > 0:
            return (row['moving_time'] / row['distance']) * 100
        return None

    year_df['pace_per_100m'] = year_df.apply(_pace_sec, axis=1)
    year_df['pace_per_100yd'] = year_df['pace_per_100m'].apply(
        lambda p: p * 0.9144 if p else None  # 100m pace × 0.9144 = 100yd pace
    )
    year_df = year_df.rename(columns={'distance': 'meters'})
    return (
        year_df[['start_date_local', 'name', 'meters', 'yards',
                  'moving_time', 'hours', 'pace_per_100m', 'pace_per_100yd']]
        .sort_values('start_date_local', ascending=False)
        .reset_index(drop=True)
    )


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


# ---------------------------------------------------------------------------
# Equity helpers
# ---------------------------------------------------------------------------

_EQ_PATTERN = r'^[A-Za-z\[]*[Ee][Qq](\s|\d|$)'


def aggregate_equity_by_year(df, settings):
    """
    Equity miles per year from ACTUAL activities, broken down by bike / ski / swim.
    Activities whose names match the *Eq pattern are excluded from all sports —
    they are the user's manual equity declarations, not actual recorded activities.
    """
    swim_rate  = settings.get('conversions', {}).get('swim_meters_per_mile', 100)
    ski_rate   = settings.get('conversions', {}).get('ski_vert_per_mile', 1000)
    bike_types = ['Ride', 'VirtualRide', 'EBikeRide']
    ski_types  = ['AlpineSki', 'BackcountrySki', 'NordicSki', 'Snowboard']
    swim_types = ['Swim']

    df = df.copy()
    df['_is_eq'] = df['name'].str.match(_EQ_PATTERN, na=False)

    rows = []
    for year in sorted(df['year'].unique()):
        y = df[df['year'] == year]
        bike = y[y['final_type'].isin(bike_types) & ~y['_is_eq']]['distance_miles'].sum()
        ski  = y[y['final_type'].isin(ski_types)  & ~y['_is_eq']]['elevation_feet'].sum() / ski_rate
        swim = y[y['final_type'].isin(swim_types) & ~y['_is_eq']]['distance'].sum() / swim_rate
        rows.append({'year': year, 'bike': bike, 'ski': ski, 'swim': swim,
                     'total': bike + ski + swim})
    return pd.DataFrame(rows)


def aggregate_equity_by_month(df, year, settings):
    """
    Equity miles per month (12 rows, 0-filled) for the given year.
    Eq-named activities are excluded from all sports — they are manual declarations.
    """
    import calendar as cal
    swim_rate  = settings.get('conversions', {}).get('swim_meters_per_mile', 100)
    ski_rate   = settings.get('conversions', {}).get('ski_vert_per_mile', 1000)
    bike_types = ['Ride', 'VirtualRide', 'EBikeRide']
    ski_types  = ['AlpineSki', 'BackcountrySki', 'NordicSki', 'Snowboard']
    swim_types = ['Swim']

    y_df = df[df['year'] == year].copy()
    y_df['_is_eq'] = y_df['name'].str.match(_EQ_PATTERN, na=False)
    y_df['month'] = y_df['start_date_local'].dt.month

    rows = []
    for m in range(1, 13):
        mo = y_df[y_df['month'] == m]
        bike = mo[mo['final_type'].isin(bike_types) & ~mo['_is_eq']]['distance_miles'].sum()
        ski  = mo[mo['final_type'].isin(ski_types)  & ~mo['_is_eq']]['elevation_feet'].sum() / ski_rate
        swim = mo[mo['final_type'].isin(swim_types) & ~mo['_is_eq']]['distance'].sum() / swim_rate
        rows.append({
            'month': m,
            'month_name': cal.month_abbr[m],
            'bike': bike, 'ski': ski, 'swim': swim,
            'total': bike + ski + swim,
        })
    return pd.DataFrame(rows)


def compute_period_stats(df):
    """
    Compute wrapped-style stats for any pre-filtered period.
    Unlike compute_wrapped_stats, this accepts an arbitrary df slice —
    no specific year required, no prior-year comparison.
    """
    import calendar as cal

    if df.empty:
        return {}

    df = df.copy()

    totals = {
        'activities': len(df),
        'miles':      df['distance_miles'].sum(),
        'hours':      df['moving_time'].sum() / 3600,
        'vert_ft':    df['elevation_feet'].sum(),
    }

    # Sport breakdown
    sport = (
        df.groupby('final_type')
        .agg(
            activities=('id', 'count'),
            miles=('distance_miles', 'sum'),
            hours=('moving_time', lambda x: x.sum() / 3600),
            vert_ft=('elevation_feet', 'sum'),
        )
        .reset_index()
        .sort_values('activities', ascending=False)
    )

    # Longest single activity by distance
    la_row = df.loc[df['distance_miles'].idxmax()]
    longest_activity = {
        'name':  la_row['name'],
        'date':  la_row['start_date_local'].date(),
        'miles': la_row['distance_miles'],
        'type':  la_row['final_type'],
    }

    # Biggest week by distance
    iso = df['start_date_local'].dt.isocalendar()
    df['_iso_year'] = iso['year'].values
    df['_iso_week'] = iso['week'].values
    weekly = df.groupby(['_iso_year', '_iso_week'])['distance_miles'].sum()
    if not weekly.empty:
        bw_idx = weekly.idxmax()
        biggest_week = {'miles': weekly.max(), 'label': f"Week {bw_idx[1]}, {bw_idx[0]}"}
    else:
        biggest_week = {'miles': 0, 'label': 'N/A'}

    # Best vert day
    df['_date'] = df['start_date_local'].dt.date
    daily_vert = df.groupby('_date')['elevation_feet'].sum()
    bv_date = daily_vert.idxmax()
    best_vert_day = {'date': bv_date, 'vert_ft': daily_vert.max()}

    # Longest consecutive active streak
    dates = sorted(df['_date'].unique())
    max_streak = cur_streak = (1 if dates else 0)
    for i in range(1, len(dates)):
        if (dates[i] - dates[i - 1]).days == 1:
            cur_streak += 1
            max_streak = max(max_streak, cur_streak)
        else:
            cur_streak = 1

    # Fun facts
    fun_facts = {
        'everests':    totals['vert_ft'] / 29032,
        'earth_pct':   totals['miles'] / 24901 * 100,
        'days_moving': totals['hours'] / 24,
    }

    # Yearly breakdown
    yearly = (
        df.groupby('year')
        .agg(
            miles=('distance_miles', 'sum'),
            hours=('moving_time', lambda x: x.sum() / 3600),
            count=('id', 'count'),
        )
        .reset_index()
        .sort_values('year')
    )

    # Monthly breakdown — labels include year suffix when multiple years present
    df['_month'] = df['start_date_local'].dt.month
    multi_year = len(df['year'].unique()) > 1
    monthly = (
        df.groupby(['year', '_month'])
        .agg(
            miles=('distance_miles', 'sum'),
            hours=('moving_time', lambda x: x.sum() / 3600),
            count=('id', 'count'),
        )
        .reset_index()
        .sort_values(['year', '_month'])
    )
    monthly['month_label'] = monthly.apply(
        lambda r: f"{cal.month_abbr[int(r['_month'])]} '{str(int(r['year']))[-2:]}"
                  if multi_year else cal.month_abbr[int(r['_month'])],
        axis=1,
    )

    return {
        'totals':           totals,
        'sport_breakdown':  sport,
        'longest_activity': longest_activity,
        'biggest_week':     biggest_week,
        'best_vert_day':    best_vert_day,
        'longest_streak':   max_streak,
        'fun_facts':        fun_facts,
        'yearly':           yearly,
        'monthly':          monthly,
    }


def get_longest_activities(df, sort_col='distance_miles', n=20):
    """Returns the top n activities sorted by sort_col descending."""
    if df.empty:
        return pd.DataFrame()
    return df.nlargest(n, sort_col).reset_index(drop=True)


def aggregate_recent_months_by_sport(df, sport, n_months):
    """
    Returns a DataFrame comparing this year vs last year for the last n_months
    calendar months (ending with the current month).

    Columns: calendar_year, month_num, month_abbr, month_label,
             this_year_val, last_year_val, is_current.

    sport: 'bike' | 'bike_equity' | 'swim' | 'ski'
    Units: bike/bike_equity → miles, swim → meters, ski → vertical feet.
    """
    import calendar as cal
    from datetime import date as _date

    today = _date.today()

    # Build month window oldest→newest
    months = []
    y, m = today.year, today.month
    for _ in range(n_months):
        months.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    months.reverse()

    bike_types = ['Ride', 'VirtualRide', 'EBikeRide']
    ski_types  = ['AlpineSki', 'BackcountrySki', 'NordicSki', 'Snowboard']
    swim_types = ['Swim']

    df = df.copy()
    df['_m'] = df['start_date_local'].dt.month
    df['_y'] = df['start_date_local'].dt.year

    if sport == 'bike_equity':
        df['_is_eq'] = df['name'].str.match(_EQ_PATTERN, na=False)
        sport_df  = df[df['final_type'].isin(bike_types) & ~df['_is_eq']]
        value_col = 'distance_miles'
    elif sport == 'bike':
        sport_df  = df[df['final_type'].isin(bike_types)]
        value_col = 'distance_miles'
    elif sport == 'swim':
        sport_df  = df[df['final_type'].isin(swim_types)]
        value_col = 'distance'
    elif sport == 'ski':
        sport_df  = df[df['final_type'].isin(ski_types)]
        value_col = 'elevation_feet'
    else:
        return pd.DataFrame()

    def _total(year, month):
        mask = (sport_df['_y'] == year) & (sport_df['_m'] == month)
        return float(sport_df.loc[mask, value_col].sum())

    rows = []
    years_seen = set()
    for (y, m) in months:
        years_seen.add(y)
        rows.append({
            'calendar_year': y,
            'month_num':     m,
            'month_abbr':    cal.month_abbr[m],
            'this_year_val': _total(y, m),
            'last_year_val': _total(y - 1, m),
            'is_current':    (y == today.year and m == today.month),
        })

    result = pd.DataFrame(rows)
    # Include year suffix in label only when the window spans two calendar years
    if len(years_seen) > 1:
        result['month_label'] = result.apply(
            lambda r: f"{r['month_abbr']} '{str(r['calendar_year'])[-2:]}", axis=1
        )
    else:
        result['month_label'] = result['month_abbr']

    return result


def get_eq_activities(df):
    """
    Returns all activities whose names match the *Eq pattern, sorted by date descending.
    Columns: date, name, final_type, eq_prefix, miles, year, month.
    """
    import re
    eq_df = df[df['name'].str.match(_EQ_PATTERN, na=False)].copy()
    if eq_df.empty:
        return eq_df

    def _prefix(name):
        m = re.match(r'^([A-Za-z\[]*)[Ee][Qq]', str(name))
        return m.group(1).upper() if m else ''

    eq_df['eq_prefix'] = eq_df['name'].apply(_prefix)
    eq_df['date']  = eq_df['start_date_local'].dt.date
    eq_df['month'] = eq_df['start_date_local'].dt.month
    return (
        eq_df[['date', 'name', 'final_type', 'eq_prefix', 'distance_miles', 'year', 'month']]
        .rename(columns={'distance_miles': 'miles'})
        .sort_values('date', ascending=False)
        .reset_index(drop=True)
    )