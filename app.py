"""
Strava Stats — Interactive Streamlit Dashboard
Multi-tab layout built with Plotly charts.
"""
import json
import os
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from src import config, process_data
from src.charts import (
    make_equity_annual_chart,
    make_equity_monthly_chart,
    make_monthly_chart,
    make_period_comparison_chart,
    make_recent_months_chart,
    make_season_vert_chart,
    make_sport_breakdown_chart,
    make_swim_year_chart,
    make_year_dist_chart,
    make_year_time_chart,
)
from src.config import BIKE_TYPES, GEAR_FALLBACKS, SKI_TYPES, SWIM_TYPES

# ---------------------------------------------------------------------------
# Page config (must be first Streamlit call)
# ---------------------------------------------------------------------------
st.set_page_config(layout="wide", page_title="Strava Stats")


# ---------------------------------------------------------------------------
# Data loading (cached)
# ---------------------------------------------------------------------------
@st.cache_data
def load_activities():
    """
    Load the flat activity archive and merge any per-year JSON files whose
    year is NOT already represented in the main archive, then process.
    """
    archive_path = config.ACTIVITIES_FILE
    all_activities = []

    if os.path.exists(archive_path):
        with open(archive_path, 'r') as f:
            all_activities = json.load(f)

    # Determine which years are already in the archive
    present_years = set()
    for act in all_activities:
        sd = act.get('start_date', '') or act.get('start_date_local', '')
        if sd:
            present_years.add(int(sd[:4]))

    # Merge any data/<year>.json files for years not yet in the main archive
    raw_dir = config.RAW_DIR
    for fname in os.listdir(raw_dir):
        if not fname.endswith('.json'):
            continue
        stem = fname[:-5]
        if not stem.isdigit():
            continue
        year = int(stem)
        if year in present_years:
            continue  # already covered
        fpath = os.path.join(raw_dir, fname)
        with open(fpath, 'r') as f:
            extra = json.load(f)
        if isinstance(extra, list):
            all_activities.extend(extra)

    if not all_activities:
        return pd.DataFrame()

    return process_data.process_activities(all_activities)


@st.cache_data
def load_athlete_profile():
    if os.path.exists(config.ATHLETE_PROFILE_FILE):
        with open(config.ATHLETE_PROFILE_FILE) as f:
            return json.load(f)
    return {}


@st.cache_data
def load_athlete_stats():
    if os.path.exists(config.ATHLETE_STATS_FILE):
        with open(config.ATHLETE_STATS_FILE) as f:
            return json.load(f)
    return {}


@st.cache_data
def load_settings():
    """Load settings.json, falling back to defaults for any missing keys."""
    import copy
    settings = copy.deepcopy(config.DEFAULT_SETTINGS)
    if os.path.exists(config.SETTINGS_FILE):
        with open(config.SETTINGS_FILE) as f:
            saved = json.load(f)
        for section in settings:
            if section in saved:
                settings[section].update(saved[section])
    return settings


@st.cache_data
def load_gear_map():
    """Load gear_map.json if it exists, merging GEAR_FALLBACKS as a baseline."""
    gear_map = dict(GEAR_FALLBACKS)
    path = config.GEAR_MAP_FILE
    if os.path.exists(path):
        with open(path, 'r') as f:
            live = json.load(f)
        gear_map.update(live)  # live data wins over fallbacks
    return gear_map


# ---------------------------------------------------------------------------
# ISO-week navigation helpers
# ---------------------------------------------------------------------------
def _prev_iso_week(iso_year, iso_week):
    monday = date.fromisocalendar(iso_year, iso_week, 1)
    prev_monday = monday - timedelta(weeks=1)
    cal = prev_monday.isocalendar()
    return cal[0], cal[1]


def _next_iso_week(iso_year, iso_week):
    monday = date.fromisocalendar(iso_year, iso_week, 1)
    next_monday = monday + timedelta(weeks=1)
    cal = next_monday.isocalendar()
    return cal[0], cal[1]


def _week_label(iso_year, iso_week):
    monday = date.fromisocalendar(iso_year, iso_week, 1)
    sunday = monday + timedelta(days=6)
    if monday.year == sunday.year:
        return f"Week {iso_week} ({monday.strftime('%b %-d')} – {sunday.strftime('%b %-d, %Y')})"
    return f"Week {iso_week} ({monday.strftime('%b %-d, %Y')} – {sunday.strftime('%b %-d, %Y')})"


# ---------------------------------------------------------------------------
# Default period helpers
# ---------------------------------------------------------------------------
def _last_complete_month():
    today = date.today()
    first_of_this_month = today.replace(day=1)
    last_month_end = first_of_this_month - timedelta(days=1)
    return last_month_end.year, last_month_end.month


def _last_complete_iso_week():
    today = date.today()
    cal = today.isocalendar()
    # Go back one week
    prev_monday = date.fromisocalendar(cal[0], cal[1], 1) - timedelta(weeks=1)
    pc = prev_monday.isocalendar()
    return pc[0], pc[1]


# ---------------------------------------------------------------------------
# Render functions — Year view
# ---------------------------------------------------------------------------
def render_year_view(bike_df, dist_col, dist_label):
    current_year = date.today().year
    yearly = process_data.aggregate_by_year(bike_df)
    if yearly.empty:
        st.info("No bike data found.")
        return

    col_a, col_b = st.columns(2)
    with col_a:
        st.plotly_chart(
            make_year_dist_chart(yearly, dist_col, dist_label, current_year),
            use_container_width=True,
        )
    with col_b:
        st.plotly_chart(
            make_year_time_chart(yearly, current_year),
            use_container_width=True,
        )

    # Summary metrics — totals across all years shown
    total_miles = yearly['miles'].sum()
    total_km = yearly['km'].sum()
    total_hours = yearly['hours'].sum()
    total_count = yearly['count'].sum()

    m1, m2, m3 = st.columns(3)
    dist_val = f"{total_miles:,.0f} mi" if dist_col == 'miles' else f"{total_km:,.0f} km"
    m1.metric("Total Distance", dist_val)
    m2.metric("Total Hours", f"{total_hours:,.0f} h")
    m3.metric("Total Activities", f"{int(total_count):,}")


# ---------------------------------------------------------------------------
# Render functions — Month view
# ---------------------------------------------------------------------------
def render_month_view(bike_df, dist_col, dist_label):
    import calendar
    today = date.today()
    current_year, current_month = today.year, today.month
    default_year, default_month = _last_complete_month()

    # Session state initialisation
    if 'bike_ref_year' not in st.session_state:
        st.session_state.bike_ref_year = default_year
    if 'bike_ref_month' not in st.session_state:
        st.session_state.bike_ref_month = default_month

    ref_year = st.session_state.bike_ref_year
    ref_month = st.session_state.bike_ref_month

    # Navigator row
    nav_l, nav_mid, nav_r = st.columns([1, 3, 1])
    with nav_l:
        if st.button("◀ Prev", key="month_prev"):
            ref_date = date(ref_year, ref_month, 1) - timedelta(days=1)
            st.session_state.bike_ref_year = ref_date.year
            st.session_state.bike_ref_month = ref_date.month
            st.rerun()
    with nav_mid:
        label = f"{calendar.month_name[ref_month]} {ref_year}"
        st.markdown(f"<h4 style='text-align:center;margin:0'>{label}</h4>", unsafe_allow_html=True)
    with nav_r:
        if st.button("Next ▶", key="month_next"):
            ref_date = date(ref_year, ref_month, 28) + timedelta(days=4)
            ref_date = ref_date.replace(day=1)
            st.session_state.bike_ref_year = ref_date.year
            st.session_state.bike_ref_month = ref_date.month
            st.rerun()

    # Aggregate data
    ref_df = process_data.aggregate_by_month(bike_df, ref_year, ref_month)
    prior_df = process_data.aggregate_by_month(bike_df, ref_year - 1, ref_month)

    # Shadow = current month only if different from ref
    is_current = (ref_year == current_year and ref_month == current_month)
    shadow_df = None
    if not is_current:
        shadow_df = process_data.aggregate_by_month(bike_df, current_year, current_month)

    fig = make_period_comparison_chart(
        ref_df=ref_df,
        prior_df=prior_df,
        shadow_df=shadow_df,
        x_col='day',
        x_label='Day of Month',
        dist_col=dist_col,
        dist_label=dist_label,
        title=f"{calendar.month_name[ref_month]} {ref_year} vs prior year"
              + ("" if is_current else f" + {calendar.month_name[current_month]} {current_year} (current)"),
    )
    st.plotly_chart(fig, use_container_width=True)

    # Stats panels
    ref_stats = process_data.get_period_stats(bike_df, ref_year, month=ref_month)
    prior_stats = process_data.get_period_stats(bike_df, ref_year - 1, month=ref_month)

    stat_cols = st.columns(3 if not is_current else 2)
    _render_stat_block(stat_cols[0], f"{calendar.month_name[ref_month]} {ref_year}", ref_stats, dist_col)
    _render_stat_block(stat_cols[1], f"{calendar.month_name[ref_month]} {ref_year - 1}", prior_stats, dist_col)
    if not is_current:
        shadow_stats = process_data.get_period_stats(bike_df, current_year, month=current_month)
        _render_stat_block(stat_cols[2], f"{calendar.month_name[current_month]} {current_year} (YTD)", shadow_stats, dist_col)


# ---------------------------------------------------------------------------
# Render functions — Week view
# ---------------------------------------------------------------------------
def render_week_view(bike_df, dist_col, dist_label):
    today = date.today()
    current_cal = today.isocalendar()
    current_iso_year, current_iso_week = current_cal[0], current_cal[1]
    default_iso_year, default_iso_week = _last_complete_iso_week()

    # Session state initialisation
    if 'bike_ref_iso_year' not in st.session_state:
        st.session_state.bike_ref_iso_year = default_iso_year
    if 'bike_ref_iso_week' not in st.session_state:
        st.session_state.bike_ref_iso_week = default_iso_week

    ref_iso_year = st.session_state.bike_ref_iso_year
    ref_iso_week = st.session_state.bike_ref_iso_week

    # Navigator row
    nav_l, nav_mid, nav_r = st.columns([1, 3, 1])
    with nav_l:
        if st.button("◀ Prev", key="week_prev"):
            y, w = _prev_iso_week(ref_iso_year, ref_iso_week)
            st.session_state.bike_ref_iso_year = y
            st.session_state.bike_ref_iso_week = w
            st.rerun()
    with nav_mid:
        st.markdown(
            f"<h4 style='text-align:center;margin:0'>{_week_label(ref_iso_year, ref_iso_week)}</h4>",
            unsafe_allow_html=True,
        )
    with nav_r:
        if st.button("Next ▶", key="week_next"):
            y, w = _next_iso_week(ref_iso_year, ref_iso_week)
            st.session_state.bike_ref_iso_year = y
            st.session_state.bike_ref_iso_week = w
            st.rerun()

    # Aggregate data
    ref_df = process_data.aggregate_by_iso_week(bike_df, ref_iso_year, ref_iso_week)
    prior_df = process_data.aggregate_by_iso_week(bike_df, ref_iso_year - 1, ref_iso_week)

    is_current = (ref_iso_year == current_iso_year and ref_iso_week == current_iso_week)
    shadow_df = None
    if not is_current:
        shadow_df = process_data.aggregate_by_iso_week(bike_df, current_iso_year, current_iso_week)

    fig = make_period_comparison_chart(
        ref_df=ref_df,
        prior_df=prior_df,
        shadow_df=shadow_df,
        x_col='day_label',
        x_label='Day',
        dist_col=dist_col,
        dist_label=dist_label,
        title=_week_label(ref_iso_year, ref_iso_week)
              + ("" if is_current else f" + current week (in progress)"),
    )
    st.plotly_chart(fig, use_container_width=True)

    # Stats panels
    ref_stats = process_data.get_period_stats(bike_df, ref_iso_year, iso_week=ref_iso_week)
    prior_stats = process_data.get_period_stats(bike_df, ref_iso_year - 1, iso_week=ref_iso_week)

    stat_cols = st.columns(3 if not is_current else 2)
    _render_stat_block(stat_cols[0], _week_label(ref_iso_year, ref_iso_week), ref_stats, dist_col)
    _render_stat_block(stat_cols[1], _week_label(ref_iso_year - 1, ref_iso_week) + " (prior)", prior_stats, dist_col)
    if not is_current:
        shadow_stats = process_data.get_period_stats(bike_df, current_iso_year, iso_week=current_iso_week)
        _render_stat_block(stat_cols[2], "Current week (in progress)", shadow_stats, dist_col)


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------
def _render_stat_block(col, label, stats, dist_col):
    dist_val = f"{stats['miles']:,.1f} mi" if dist_col == 'miles' else f"{stats['km']:,.1f} km"
    with col:
        st.markdown(f"**{label}**")
        st.metric("Distance", dist_val)
        st.metric("Hours", f"{stats['hours']:,.1f} h")
        st.metric("Rides", str(int(stats['count'])))


# ---------------------------------------------------------------------------
# Bike tab
# ---------------------------------------------------------------------------
def render_bike_tab(bike_df, gear_map):
    # --- Controls row ---
    ctrl_l, ctrl_r = st.columns(2)
    with ctrl_l:
        time_mode = st.radio(
            "Time mode", ["Year", "Month", "Week"],
            horizontal=True, key="bike_time_mode",
        )
    with ctrl_r:
        unit = st.radio(
            "Units", ["Miles", "Km"],
            horizontal=True, key="bike_unit",
        )

    dist_col = 'miles' if unit == 'Miles' else 'km'
    dist_label = 'Miles' if unit == 'Miles' else 'Km'

    # --- Gear checkboxes ---
    st.markdown("**Filter by bike:**")
    gear_ids = sorted(
        bike_df['gear_id'].unique().tolist(),
        key=lambda g: gear_map.get(g, g or '') if g else '',
    )

    selected_gears = []
    gear_cols = st.columns(min(len(gear_ids), 4))
    for i, gid in enumerate(gear_ids):
        label = gear_map.get(gid, gid) if gid else "Unknown Bike"
        key = f"bike_gear_{gid}"
        checked = gear_cols[i % len(gear_cols)].checkbox(label, value=True, key=key)
        if checked:
            selected_gears.append(gid)

    # Filter bike_df by selected gears
    filtered_df = bike_df[bike_df['gear_id'].isin(selected_gears)]

    st.divider()

    # --- Dispatch to view ---
    if time_mode == "Year":
        render_year_view(filtered_df, dist_col, dist_label)
    elif time_mode == "Month":
        render_month_view(filtered_df, dist_col, dist_label)
    elif time_mode == "Week":
        render_week_view(filtered_df, dist_col, dist_label)


# ---------------------------------------------------------------------------
# Ski tab
# ---------------------------------------------------------------------------
def render_ski_tab(ski_df, settings):
    if ski_df.empty:
        st.info("No ski activities found in the archive.")
        return

    goal_vert = settings['goals']['ski_season_vert_ft']
    ski_vert_per_mile = settings['conversions']['ski_vert_per_mile']

    today = date.today()
    current_season_key = today.year if today.month >= 10 else today.year - 1

    seasonal_df = process_data.aggregate_ski_by_season(ski_df)

    # --- All-seasons chart ---
    st.plotly_chart(
        make_season_vert_chart(seasonal_df, current_season_key, goal_vert=goal_vert),
        use_container_width=True,
    )

    # --- Season selector ---
    season_labels = seasonal_df['season_label'].tolist()[::-1]   # newest first
    season_keys = seasonal_df['season_key'].tolist()[::-1]

    default_idx = 0
    if current_season_key in season_keys:
        default_idx = season_keys.index(current_season_key)
    elif season_keys:
        default_idx = 0  # most recent available

    selected_label = st.selectbox("Season", season_labels, index=default_idx, key="ski_season")
    selected_key = season_keys[season_labels.index(selected_label)]

    st.divider()

    # --- Season summary metrics ---
    row = seasonal_df[seasonal_df['season_key'] == selected_key].iloc[0]
    equity_miles = row['vert_ft'] / ski_vert_per_mile if ski_vert_per_mile > 0 else 0
    avg_vert = row['vert_ft'] / row['days'] if row['days'] > 0 else 0

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Days on snow", int(row['days']))
    m2.metric("Sessions", int(row['sessions']))
    m3.metric("Total vert", f"{row['vert_ft']:,.0f} ft")
    m4.metric("Avg vert / day", f"{avg_vert:,.0f} ft")
    m5.metric("Equity miles", f"{equity_miles:,.0f} mi")

    # Goal progress bar (show for any season, not just current)
    if goal_vert > 0:
        progress = min(row['vert_ft'] / goal_vert, 1.0)
        pct = progress * 100
        label = f"Season goal: {row['vert_ft']:,.0f} / {goal_vert:,.0f} ft ({pct:.0f}%)"
        st.progress(progress, text=label)

    st.divider()

    # --- Ski days table ---
    st.subheader(f"{selected_label} — Ski Days")
    days_df = process_data.get_ski_days_table(ski_df, selected_key)

    if days_df.empty:
        st.info("No ski days recorded for this season yet.")
        return

    # Format for display
    display = days_df.copy()
    display['date'] = pd.to_datetime(display['date']).dt.strftime('%a %b %-d, %Y')
    display['vert_ft'] = display['vert_ft'].apply(lambda x: f"{x:,.0f}")
    display['distance_mi'] = display['distance_mi'].apply(lambda x: f"{x:.1f}")
    display['hours'] = display['hours'].apply(lambda x: f"{x:.1f}")
    display = display.drop(columns=['sessions'])
    display = display.rename(columns={
        'date': 'Date',
        'activity': 'Activity',
        'type': 'Type',
        'vert_ft': 'Vert (ft)',
        'distance_mi': 'Dist (mi)',
        'hours': 'Hours',
    })
    st.dataframe(display, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Swim tab
# ---------------------------------------------------------------------------
def _fmt_time(seconds):
    """Format seconds as H:MM:SS or M:SS."""
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _fmt_pace(pace_sec):
    """Format pace (seconds per 100m/yd) as M:SS."""
    if pace_sec is None or pace_sec <= 0:
        return "—"
    m, s = divmod(int(pace_sec), 60)
    return f"{m}:{s:02d}"


def render_swim_tab(swim_df, settings):
    if swim_df.empty:
        st.info("No swim activities found in the archive.")
        return

    monthly_goal_m = settings['goals']['swim_monthly_meters']

    # --- Controls ---
    ctrl_l, ctrl_r = st.columns(2)
    with ctrl_l:
        years = sorted(swim_df['year'].unique().tolist(), reverse=True)
        selected_year = st.selectbox("Year", years, key="swim_year")
    with ctrl_r:
        unit = st.radio("Units", ["Meters", "Yards"], horizontal=True, key="swim_unit")

    dist_col   = 'meters' if unit == 'Meters' else 'yards'
    pace_col   = 'pace_per_100m' if unit == 'Meters' else 'pace_per_100yd'
    dist_label = 'm' if unit == 'Meters' else 'yd'
    pace_label = '/100m' if unit == 'Meters' else '/100yd'
    mult       = 1.0 if unit == 'Meters' else 1.09361
    goal_val   = monthly_goal_m * mult
    annual_goal_val = goal_val * 12

    current_year = date.today().year

    # --- Annual chart + monthly breakdown side by side ---
    yearly  = process_data.aggregate_swim_by_year(swim_df)
    monthly = process_data.aggregate_swim_by_month(swim_df, selected_year)

    col_l, col_r = st.columns(2)
    with col_l:
        # Pass the right column name to the chart
        yearly_plot = yearly.rename(columns={dist_col: '_dist'})[['year', 'swims', '_dist']].copy()
        yearly_plot.columns = ['year', 'swims', dist_col]
        st.plotly_chart(
            make_swim_year_chart(yearly_plot, current_year, annual_goal=annual_goal_val),
            use_container_width=True,
        )
    with col_r:
        st.plotly_chart(
            make_monthly_chart(monthly, dist_col, dist_label, goal=goal_val),
            use_container_width=True,
        )

    st.divider()

    # --- Year stats ---
    year_row = yearly[yearly['year'] == selected_year]
    if not year_row.empty:
        row = year_row.iloc[0]
        total_dist = row[dist_col]
        total_swims = int(row['swims'])
        avg_dist = row['avg_meters'] * mult
        months_with_data = int((monthly[dist_col] > 0).sum())
        avg_monthly = total_dist / months_with_data if months_with_data else 0

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total Distance", f"{total_dist:,.0f} {dist_label}")
        m2.metric("Swims", f"{total_swims}")
        m3.metric("Avg per Swim", f"{avg_dist:,.0f} {dist_label}")
        m4.metric("Avg per Month", f"{avg_monthly:,.0f} {dist_label}")

        # Monthly goal progress (against average achieved)
        if goal_val > 0:
            progress = min(avg_monthly / goal_val, 1.0)
            st.progress(progress,
                text=f"Monthly goal pace: {avg_monthly:,.0f} / {goal_val:,.0f} {dist_label} avg ({progress*100:.0f}%)")

    st.divider()

    # --- Swim log ---
    st.subheader(f"{selected_year} — Swim Log")
    log_df = process_data.get_swim_log(swim_df, selected_year)

    if log_df.empty:
        st.info("No swims recorded for this year.")
        return

    display = log_df.copy()
    display['Date']     = pd.to_datetime(display['start_date_local']).dt.strftime('%-m/%-d/%Y')
    display['Activity'] = display['name']
    display[f'Dist ({dist_label})'] = (display[dist_col]).apply(lambda x: f"{x:,.0f}")
    display['Time']     = display['moving_time'].apply(_fmt_time)
    display[f'Pace ({pace_label})'] = display[pace_col].apply(_fmt_pace)

    display = display[['Date', 'Activity', f'Dist ({dist_label})', 'Time', f'Pace ({pace_label})']]
    st.dataframe(display, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Mile Equity tab
# ---------------------------------------------------------------------------
def render_equity_tab(df, settings):
    convs         = settings.get('conversions', {})
    goals         = settings.get('goals', {})
    swim_rate     = convs.get('swim_meters_per_mile', 100)
    ski_rate      = convs.get('ski_vert_per_mile', 1000)
    annual_goal   = goals.get('annual_equity_miles', 0)
    monthly_goal  = goals.get('monthly_equity_miles', 0)

    available_years = sorted(df['year'].unique().tolist(), reverse=True)
    today = date.today()
    # Default to the most recently completed year (prior year)
    default_year = today.year - 1
    default_idx = available_years.index(default_year) if default_year in available_years else 0
    selected_year = st.selectbox("Year", available_years, index=default_idx, key="equity_year")

    current_year = today.year
    yearly  = process_data.aggregate_equity_by_year(df, settings)
    monthly = process_data.aggregate_equity_by_month(df, selected_year, settings)

    # --- Charts ---
    col_l, col_r = st.columns(2)
    with col_l:
        st.plotly_chart(
            make_equity_annual_chart(yearly, current_year),
            use_container_width=True,
        )
    with col_r:
        st.plotly_chart(
            make_equity_monthly_chart(monthly, goal=monthly_goal),
            use_container_width=True,
        )

    st.divider()

    # --- Selected year metrics ---
    yr_row = yearly[yearly['year'] == selected_year]
    if yr_row.empty:
        st.info("No data for the selected year.")
        return

    bike_eq  = yr_row['bike'].values[0]
    ski_eq   = yr_row['ski'].values[0]
    swim_eq  = yr_row['swim'].values[0]
    total_eq = yr_row['total'].values[0]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Equity Miles", f"{total_eq:,.0f}")
    pct_str = lambda v: f"{v / total_eq * 100:.0f}% of total" if total_eq else None
    c2.metric("Bike",  f"{bike_eq:,.0f} mi",  pct_str(bike_eq),  delta_color="off")
    c3.metric("Ski",   f"{ski_eq:,.0f} mi",   pct_str(ski_eq),   delta_color="off")
    c4.metric("Swim",  f"{swim_eq:,.0f} mi",  pct_str(swim_eq),  delta_color="off")

    if annual_goal:
        pct = min(total_eq / annual_goal, 1.0)
        st.caption(f"Annual goal: {total_eq:,.0f} / {annual_goal:,} equity miles ({pct * 100:.0f}%)")
        st.progress(pct)

    st.caption(
        f"Conversion rates — Bike: 1 mi = 1 equity mi  ·  "
        f"Swim: {swim_rate:,.0f} m = 1 equity mi  ·  "
        f"Ski: {ski_rate:,.0f} vert ft = 1 equity mi"
    )

    st.divider()

    # --- Manual Eq activities table ---
    st.subheader("Manual Eq Activities")
    st.caption(
        "Activities whose names include an Eq marker (e.g. SEq, HEq, GEq) are the user's "
        "manual equity declarations. They are listed here for review but excluded from the "
        "calculated equity above to avoid double-counting with actual activity data."
    )

    eq_df = process_data.get_eq_activities(df)
    if eq_df.empty:
        st.info("No Eq activities found.")
        return

    # Filter to selected year
    year_options = ["All years"] + [str(y) for y in sorted(eq_df['year'].unique(), reverse=True)]
    eq_year_sel = st.selectbox("Filter by year", year_options, key="eq_year_filter")
    if eq_year_sel != "All years":
        show_eq = eq_df[eq_df['year'] == int(eq_year_sel)].copy()
    else:
        show_eq = eq_df.copy()

    # Summary counts by prefix
    summary_cols = st.columns(4)
    prefix_counts = show_eq.groupby('eq_prefix')['miles'].agg(['count', 'sum'])
    for i, (prefix, row) in enumerate(prefix_counts.iterrows()):
        summary_cols[i % 4].metric(
            f"{prefix}Eq" if prefix else "Eq",
            f"{row['count']:.0f} activities",
            f"{row['sum']:,.0f} mi",
            delta_color="off",
        )

    display = show_eq.copy()
    display['Date']     = pd.to_datetime(display['date']).dt.strftime('%-m/%-d/%Y')
    display['Activity'] = display['name']
    display['Type']     = display['final_type']
    display['Prefix']   = display['eq_prefix'].apply(lambda p: f"{p}Eq" if p else "Eq")
    display['Miles']    = display['miles'].apply(lambda v: f"{v:,.1f}")
    st.dataframe(
        display[['Date', 'Activity', 'Type', 'Prefix', 'Miles']],
        use_container_width=True,
        hide_index=True,
    )


# ---------------------------------------------------------------------------
# Wrapped tab
# ---------------------------------------------------------------------------
def render_wrapped_tab(df, settings, athlete_profile):
    # --- Year selector ---
    available_years = sorted(df['year'].unique().tolist(), reverse=True)
    today = date.today()
    default_year = today.year - 1
    default_idx = available_years.index(default_year) if default_year in available_years else 0
    selected_year = st.selectbox("Year", available_years, index=default_idx, key="wrapped_year")

    stats = process_data.compute_wrapped_stats(df, selected_year)
    if not stats:
        st.info("No data for the selected year.")
        return

    curr = stats['totals']
    prev = stats['prior_totals']

    def _delta(curr_val, prev_val):
        if prev_val == 0:
            return None
        pct = (curr_val - prev_val) / prev_val * 100
        return f"{pct:+.0f}% vs {selected_year - 1}"

    # --- Headline name (if profile loaded) ---
    if athlete_profile.get('firstname'):
        name = athlete_profile['firstname']
        loc = f" · {athlete_profile.get('city', '')}, {athlete_profile.get('state', '')}".rstrip(', ')
        st.markdown(f"### {name}'s {selected_year}{loc}")
    else:
        st.markdown(f"### {selected_year} Year in Review")

    # --- Headline stats ---
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Activities",    f"{curr['activities']:,}",      _delta(curr['activities'], prev['activities']))
    c2.metric("Miles",         f"{curr['miles']:,.0f}",        _delta(curr['miles'],      prev['miles']))
    c3.metric("Hours",         f"{curr['hours']:,.0f}",        _delta(curr['hours'],      prev['hours']))
    c4.metric("Elevation (ft)",f"{curr['vert_ft']:,.0f}",      _delta(curr['vert_ft'],   prev['vert_ft']))

    # Follower snapshot from profile
    if athlete_profile.get('follower_count'):
        f1, f2, f3 = st.columns([1, 1, 2])
        f1.metric("Followers",  athlete_profile['follower_count'])
        f2.metric("Following",  athlete_profile['friend_count'])

    st.divider()

    # --- Monthly + sport charts ---
    col_l, col_r = st.columns(2)
    with col_l:
        st.plotly_chart(
            make_monthly_chart(stats['monthly'], 'miles', 'Miles'),
            use_container_width=True,
        )
    with col_r:
        st.plotly_chart(
            make_sport_breakdown_chart(stats['sport_breakdown'], 'miles', 'Miles'),
            use_container_width=True,
        )

    st.divider()

    # --- Highlights ---
    st.subheader("Highlights")
    h1, h2, h3, h4 = st.columns(4)

    bw = stats['biggest_week']
    h1.metric("Biggest Week", f"{bw['miles']:,.0f} mi", bw['label'])

    la = stats['longest_activity']
    la_name = la['name'] if len(la['name']) <= 28 else la['name'][:25] + "..."
    h2.metric("Longest Activity", f"{la['miles']:,.1f} mi", la_name)

    bvd = stats['best_vert_day']
    h3.metric("Most Vert in a Day", f"{bvd['vert_ft']:,.0f} ft", str(bvd['date']))

    h4.metric("Longest Active Streak", f"{stats['longest_streak']} days")

    st.divider()

    # --- Social & Achievements ---
    st.subheader("Social & Achievements")
    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Kudos Received",   f"{stats['kudos']['total']:,}")
    s2.metric("Group Activities", f"{stats['group_rides']:,}")
    s3.metric("Personal Records", f"{stats['achievements']['prs']:,}")
    s4.metric("Achievements",     f"{stats['achievements']['total']:,}")

    mk = stats['kudos']['most_kudoed']
    if mk and mk['kudos'] > 0:
        st.caption(f"Most kudoed: **{mk['name']}** — {mk['kudos']} kudos  ·  {mk['date']}")

    st.divider()

    # --- Fun Facts ---
    st.subheader("Fun Facts")
    ff = stats['fun_facts']
    f1, f2, f3 = st.columns(3)
    f1.metric("Everests Climbed",   f"{ff['everests']:.1f}",
              f"{curr['vert_ft']:,.0f} ft total")
    f2.metric("Around the Earth",   f"{ff['earth_pct']:.1f}%",
              f"{curr['miles']:,.0f} miles")
    f3.metric("Days in Motion",     f"{ff['days_moving']:.1f}",
              f"{curr['hours']:,.0f} hours total")


# ---------------------------------------------------------------------------
# Trends tab
# ---------------------------------------------------------------------------
def render_trends_tab(df):
    today = date.today()
    this_year = today.year
    last_year = this_year - 1

    sport_configs = {
        "Bike":        ("bike",        "Miles"),
        "Bike Equity": ("bike_equity", "Equity Miles"),
        "Swim":        ("swim",        "Meters"),
        "Ski":         ("ski",         "Vertical Feet"),
    }

    ctrl_l, ctrl_r = st.columns([3, 1])
    with ctrl_l:
        sport_label = st.radio(
            "Sport", list(sport_configs.keys()),
            horizontal=True, key="trends_sport",
        )
    with ctrl_r:
        n_months = st.slider(
            "Months", min_value=2, max_value=12, value=3,
            key="trends_n_months",
        )

    sport_key, unit_label = sport_configs[sport_label]

    months_df = process_data.aggregate_recent_months_by_sport(df, sport_key, n_months)
    if months_df.empty:
        st.info("No data found for the selected sport.")
        return

    max_year = int(months_df['calendar_year'].max())

    st.plotly_chart(
        make_recent_months_chart(months_df, max_year, max_year - 1, unit_label),
        use_container_width=True,
    )

    st.divider()

    # Highlight cards: last complete month + current month YTD
    complete_rows = months_df[~months_df['is_current']]
    current_rows  = months_df[months_df['is_current']]

    def _metric_card(col, label, row, unit_label, cmp_year):
        val  = row['this_year_val']
        prev = row['last_year_val']
        delta = val - prev
        pct   = (delta / prev * 100) if prev > 0 else None
        delta_str = f"{pct:+.0f}% vs {cmp_year}" if pct is not None else None
        col.metric(label, f"{val:,.0f} {unit_label}", delta_str)

    metric_cols = st.columns(2)
    if not complete_rows.empty:
        last = complete_rows.iloc[-1]
        _metric_card(
            metric_cols[0],
            f"{last['month_label']} {max_year}",
            last, unit_label, max_year - 1,
        )
    if not current_rows.empty:
        cur = current_rows.iloc[0]
        _metric_card(
            metric_cols[1],
            f"{cur['month_label']} {max_year} (YTD)",
            cur, unit_label, max_year - 1,
        )


# ---------------------------------------------------------------------------
# Settings tab
# ---------------------------------------------------------------------------
def render_settings_tab(settings):
    conv = settings['conversions']
    goals = settings['goals']

    # --- Conversions ---
    st.subheader("Equity Mile Conversions")
    st.caption("How each sport's effort converts to equity miles. Bike is the reference at 1:1.")

    col_bike, col_swim, col_ski = st.columns(3)

    with col_bike:
        st.markdown("**Bike**")
        st.markdown("1 mile = 1 equity mile")
        st.caption("Fixed reference — not editable")

    with col_swim:
        st.markdown("**Swim**")
        swim_rate = st.number_input(
            "meters = 1 equity mile",
            min_value=1, max_value=10000,
            value=conv['swim_meters_per_mile'],
            step=10,
            key="settings_swim_rate",
        )

    with col_ski:
        st.markdown("**Ski**")
        ski_rate = st.number_input(
            "vertical feet = 1 equity mile",
            min_value=100, max_value=10000,
            value=conv['ski_vert_per_mile'],
            step=100,
            key="settings_ski_rate",
        )

    st.divider()

    # --- Goals ---
    st.subheader("Goals")

    st.markdown("**Equity Miles**")
    gcol_annual, gcol_monthly = st.columns(2)
    with gcol_annual:
        annual_eq = st.number_input(
            "Annual equity miles",
            min_value=0, max_value=100000,
            value=goals['annual_equity_miles'],
            step=100,
            key="settings_annual_eq",
        )
    with gcol_monthly:
        monthly_eq = st.number_input(
            "Monthly equity miles",
            min_value=0, max_value=10000,
            value=goals['monthly_equity_miles'],
            step=10,
            key="settings_monthly_eq",
        )

    st.markdown("**Sport-Specific**")
    gcol_ski, gcol_swim = st.columns(2)
    with gcol_ski:
        ski_goal = st.number_input(
            "Ski season vertical feet (cumulative)",
            min_value=0, max_value=10000000,
            value=goals['ski_season_vert_ft'],
            step=10000,
            key="settings_ski_goal",
        )
    with gcol_swim:
        swim_goal = st.number_input(
            "Swim monthly meters",
            min_value=0, max_value=1000000,
            value=goals['swim_monthly_meters'],
            step=500,
            key="settings_swim_goal",
        )

    st.divider()

    if st.button("Save settings", type="primary"):
        updated = {
            'conversions': {
                'swim_meters_per_mile': swim_rate,
                'ski_vert_per_mile': ski_rate,
            },
            'goals': {
                'annual_equity_miles': annual_eq,
                'monthly_equity_miles': monthly_eq,
                'ski_season_vert_ft': ski_goal,
                'swim_monthly_meters': swim_goal,
            },
        }
        with open(config.SETTINGS_FILE, 'w') as f:
            json.dump(updated, f, indent=2)
        load_settings.clear()
        st.success("Settings saved.")
        st.rerun()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
st.title("Strava Stats")

df = load_activities()
gear_map = load_gear_map()
settings = load_settings()
athlete_profile = load_athlete_profile()

if df.empty:
    st.error("No activity data found. Run the pipeline first.")
    st.stop()

bike_df = df[df['final_type'].isin(BIKE_TYPES)].copy()
ski_df  = df[df['final_type'].isin(SKI_TYPES)].copy()
swim_df = df[df['final_type'].isin(SWIM_TYPES)].copy()

tab_bike, tab_ski, tab_swim, tab_trends, tab_equity, tab_wrapped, tab_settings = st.tabs(
    ["Bike", "Ski", "Swim", "Trends", "Mile Equity", "Wrapped", "Settings"]
)

with tab_bike:
    render_bike_tab(bike_df, gear_map)

with tab_ski:
    render_ski_tab(ski_df, settings)

with tab_swim:
    render_swim_tab(swim_df, settings)

with tab_trends:
    render_trends_tab(df)

with tab_equity:
    render_equity_tab(df, settings)

with tab_wrapped:
    render_wrapped_tab(df, settings, athlete_profile)

with tab_settings:
    render_settings_tab(settings)
