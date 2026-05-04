"""
Strava Stats — Interactive Streamlit Dashboard
Multi-tab layout built with Plotly charts.
"""
import io
import json
import os
import time as _time
import zipfile
from datetime import date, datetime as _datetime, timedelta

import pandas as pd
import streamlit as st

from src import config, process_data
from src.charts import (
    make_equity_annual_chart,
    make_equity_monthly_chart,
    make_labeled_bar_chart,
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
def _fmt_date(dt):
    """Format a date or Timestamp as M/D/YYYY without leading zeros (cross-platform)."""
    return f"{dt.month}/{dt.day}/{dt.year}"


def _fmt_date_long(dt):
    """Format a date as 'Mon Jan 5, 2025' without leading zeros (cross-platform)."""
    return dt.strftime('%a %b ') + str(dt.day) + dt.strftime(', %Y')


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
        return f"Week {iso_week} ({monday.strftime('%b ') + str(monday.day)} – {sunday.strftime('%b ') + str(sunday.day) + sunday.strftime(', %Y')})"
    return f"Week {iso_week} ({monday.strftime('%b ') + str(monday.day) + monday.strftime(', %Y')} – {sunday.strftime('%b ') + str(sunday.day) + sunday.strftime(', %Y')})"


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

    gear_ids = sorted(
        bike_df['gear_id'].unique().tolist(),
        key=lambda g: gear_map.get(g, g or '') if g else '',
    )

    # Read gear selections from session state — default to all selected on first load
    selected_gears = [
        gid for gid in gear_ids
        if st.session_state.get(f"bike_gear_{gid}", True)
    ]
    filtered_df = bike_df[bike_df['gear_id'].isin(selected_gears)]

    # --- Dispatch to view ---
    if time_mode == "Year":
        render_year_view(filtered_df, dist_col, dist_label)
    elif time_mode == "Month":
        render_month_view(filtered_df, dist_col, dist_label)
    elif time_mode == "Week":
        render_week_view(filtered_df, dist_col, dist_label)

    if unit == 'Miles':
        fmt_bike = lambda r: f"{r['distance_miles']:,.1f} mi"
    else:
        fmt_bike = lambda r: f"{r['distance'] / 1000:.1f} km"

    st.divider()
    _render_recent_table(filtered_df, fmt_bike, "Most Recent Rides", key_prefix="bike")

    st.divider()
    _render_longest_table(filtered_df, 'distance_miles', fmt_bike, "Longest Rides")

    st.divider()

    # --- Gear filter (bottom) ---
    st.markdown("**Filter by bike:**")
    gear_cols = st.columns(min(len(gear_ids), 4))
    for i, gid in enumerate(gear_ids):
        label = gear_map.get(gid, gid) if gid else "Unknown Bike"
        gear_cols[i % len(gear_cols)].checkbox(label, value=True, key=f"bike_gear_{gid}")


# ---------------------------------------------------------------------------
# Ski tab
# ---------------------------------------------------------------------------
def render_ski_tab(ski_df, settings):
    if ski_df.empty:
        st.info("No snow activities found in the archive.")
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
    st.subheader(f"{selected_label} — Snow Days")
    days_df = process_data.get_ski_days_table(ski_df, selected_key)

    if days_df.empty:
        st.info("No snow days recorded for this season yet.")
        return

    # Format for display
    display = days_df.copy()
    display['date'] = pd.to_datetime(display['date']).apply(_fmt_date_long)
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

    st.divider()
    _render_recent_table(
        ski_df,
        lambda r: f"{r['elevation_feet']:,.0f} ft vert",
        "Most Recent Snow Activities",
        key_prefix="ski",
    )

    st.divider()
    _render_longest_table(
        ski_df, 'elevation_feet',
        lambda r: f"{r['elevation_feet']:,.0f} ft",
        "Biggest Snow Days (All Seasons)",
    )


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
    display['Date']     = pd.to_datetime(display['start_date_local']).apply(_fmt_date)
    display['Activity'] = display['name']
    display[f'Dist ({dist_label})'] = (display[dist_col]).apply(lambda x: f"{x:,.0f}")
    display['Time']     = display['moving_time'].apply(_fmt_time)
    display[f'Pace ({pace_label})'] = display[pace_col].apply(_fmt_pace)

    display = display[['Date', 'Activity', f'Dist ({dist_label})', 'Time', f'Pace ({pace_label})']]
    st.dataframe(display, use_container_width=True, hide_index=True)

    if unit == 'Meters':
        fmt_swim = lambda r: f"{r['distance']:,.0f} m"
    else:
        fmt_swim = lambda r: f"{r['distance'] * 1.09361:,.0f} yd"

    st.divider()
    _render_recent_table(swim_df, fmt_swim, "Most Recent Swims", key_prefix="swim")

    st.divider()
    _render_longest_table(swim_df, 'distance', fmt_swim, "Longest Swims")


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
    display['Date']     = pd.to_datetime(display['date']).apply(_fmt_date)
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
# Period / sport filter helpers
# ---------------------------------------------------------------------------

def _build_period_options(df):
    """
    Returns (options_list, meta_dict) for the period selector.
    Default order: Last 365 days → most recent complete year → Last 30 days →
    All time → older complete years → complete months (newest first).
    """
    import calendar as cal
    today = date.today()
    current_year = today.year

    all_years = sorted(df['year'].unique().tolist())
    complete_years = [y for y in all_years if y < current_year]
    most_recent_year = complete_years[-1] if complete_years else None

    options, meta = [], {}

    def _add(key, value):
        options.append(key)
        meta[key] = value

    _add("Last 365 days", {'type': 'rolling', 'days': 365})
    if most_recent_year:
        _add(str(most_recent_year), {'type': 'year', 'year': most_recent_year})
    _add("Last 30 days", {'type': 'rolling', 'days': 30})
    _add("All time",     {'type': 'all'})

    for y in reversed(complete_years):
        if y == most_recent_year:
            continue
        _add(str(y), {'type': 'year', 'year': y})

    # Complete months with data, excluding the current month
    df_tmp = df.copy()
    df_tmp['_m'] = df_tmp['start_date_local'].dt.month
    df_tmp['_y'] = df_tmp['start_date_local'].dt.year
    pairs = sorted(df_tmp.groupby(['_y', '_m']).size().index.tolist(), reverse=True)
    for y, m in pairs:
        if y > current_year:
            continue
        if y == current_year and m >= today.month:
            continue
        key = f"{cal.month_abbr[m]} {y}"
        _add(key, {'type': 'month', 'year': y, 'month': m})

    return options, meta


def _filter_by_period(df, meta):
    """Return a copy of df filtered to the period described by meta."""
    today = date.today()
    ptype = meta['type']
    if ptype == 'rolling':
        cutoff = today - timedelta(days=meta['days'])
        return df[df['start_date_local'].dt.date >= cutoff].copy()
    elif ptype == 'year':
        return df[df['year'] == meta['year']].copy()
    elif ptype == 'month':
        return df[
            (df['year'] == meta['year']) &
            (df['start_date_local'].dt.month == meta['month'])
        ].copy()
    return df.copy()  # 'all'


_SPORT_OPTIONS = [
    "All activities",
    "Biking",
    "Skiing",
    "Swimming",
    "Equity Activities",
]


def _filter_by_sport(df, sport_key):
    """Return a copy of df filtered to the selected sport/activity group."""
    eq_pat = process_data._EQ_PATTERN
    if sport_key == "All activities":
        return df[~df['name'].str.match(eq_pat, na=False)].copy()
    elif sport_key == "Biking":
        return df[df['final_type'].isin(BIKE_TYPES) & ~df['name'].str.match(eq_pat, na=False)].copy()
    elif sport_key == "Skiing":
        return df[df['final_type'].isin(SKI_TYPES)].copy()
    elif sport_key == "Swimming":
        return df[df['final_type'].isin(SWIM_TYPES)].copy()
    elif sport_key == "Equity Activities":
        return df[df['name'].str.match(eq_pat, na=False)].copy()
    return df.copy()


# ---------------------------------------------------------------------------
# Longest activities table (shared across Bike, Swim, Ski, Wrapped tabs)
# ---------------------------------------------------------------------------

# Edit this list to add, remove, or reorder columns in the longest activities table.
_LONGEST_COLS = [
    ('Date',     'date_str'),
    ('Activity', 'name'),
    ('Type',     'final_type'),
    ('Distance', 'dist_display'),
    ('Duration', 'duration_str'),
]


def _render_recent_table(df, fmt_dist, title="Most Recent Activities", key_prefix="recent"):
    """Render the N most recent activities with a slider to control N (default 5, max 20)."""
    st.subheader(title)
    if df.empty:
        st.info("No activities to display.")
        return
    n = st.slider(
        "Number to show", min_value=1, max_value=20, value=5,
        key=f"{key_prefix}_recent_n",
    )
    recent = df.sort_values('start_date_local', ascending=False).head(n).copy()
    recent['date_str']     = pd.to_datetime(recent['start_date_local']).apply(_fmt_date)
    recent['dist_display'] = recent.apply(fmt_dist, axis=1)
    recent['duration_str'] = recent['moving_time'].apply(_fmt_time)
    src_cols = [src for _, src in _LONGEST_COLS]
    hdr_map  = {src: hdr for hdr, src in _LONGEST_COLS}
    st.dataframe(recent[src_cols].rename(columns=hdr_map), use_container_width=True, hide_index=True)


def _render_longest_table(df, sort_col, fmt_dist, title="Longest Activities", n=20):
    """
    Render a sortable table of the top n activities.
    sort_col : column to rank by (descending)
    fmt_dist : callable(row) → formatted distance string for the Distance column
    """
    st.subheader(title)
    if df.empty:
        st.info("No activities to display.")
        return
    top = df.nlargest(n, sort_col).copy()
    top['date_str']     = pd.to_datetime(top['start_date_local']).apply(_fmt_date)
    top['dist_display'] = top.apply(fmt_dist, axis=1)
    top['duration_str'] = top['moving_time'].apply(_fmt_time)
    src_cols = [src for _, src in _LONGEST_COLS]
    hdr_map  = {src: hdr for hdr, src in _LONGEST_COLS}
    st.dataframe(top[src_cols].rename(columns=hdr_map), use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Wrapped tab
# ---------------------------------------------------------------------------
def render_wrapped_tab(df, settings, athlete_profile):
    today = date.today()
    current_year = today.year

    # --- Controls ---
    period_options, period_meta = _build_period_options(df)
    c1, c2, c3 = st.columns([2, 2, 1])
    with c1:
        selected_period = st.selectbox(
            "Time period", period_options, index=0, key="wrapped_period"
        )
    with c2:
        selected_sport = st.selectbox(
            "Activities", _SPORT_OPTIONS, index=0, key="wrapped_sport"
        )
    with c3:
        view_mode = st.radio(
            "Breakdown", ["By Year", "By Month"], key="wrapped_view_mode"
        )

    # --- Filter ---
    filtered = _filter_by_period(df, period_meta[selected_period])
    filtered = _filter_by_sport(filtered, selected_sport)

    if filtered.empty:
        st.info("No activities found for the selected period and filter.")
        return

    # --- Header ---
    if athlete_profile.get('firstname'):
        name = athlete_profile['firstname']
        st.markdown(f"### {name} — {selected_period} · {selected_sport}")
    else:
        st.markdown(f"### {selected_period} · {selected_sport}")

    # --- Compute stats ---
    stats = process_data.compute_period_stats(filtered)
    curr = stats['totals']

    # --- Summary metrics ---
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Activities",     f"{curr['activities']:,}")
    m2.metric("Miles",          f"{curr['miles']:,.0f}")
    m3.metric("Hours",          f"{curr['hours']:,.0f}")
    m4.metric("Elevation (ft)", f"{curr['vert_ft']:,.0f}")

    if athlete_profile.get('follower_count'):
        f1, f2, _ = st.columns([1, 1, 2])
        f1.metric("Followers", athlete_profile['follower_count'])
        f2.metric("Following", athlete_profile['friend_count'])

    st.divider()

    # --- Charts ---
    col_l, col_r = st.columns(2)
    with col_l:
        if view_mode == "By Year":
            st.plotly_chart(
                make_year_dist_chart(stats['yearly'], 'miles', 'Miles', current_year),
                use_container_width=True,
            )
        else:
            monthly = stats['monthly']
            st.plotly_chart(
                make_labeled_bar_chart(
                    monthly['month_label'], monthly['miles'],
                    "Monthly Distance", "Month", "Miles",
                ),
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

    # --- Fun Facts ---
    st.subheader("Fun Facts")
    ff = stats['fun_facts']
    f1, f2, f3 = st.columns(3)
    f1.metric("Everests Climbed", f"{ff['everests']:.1f}",   f"{curr['vert_ft']:,.0f} ft total")
    f2.metric("Around the Earth", f"{ff['earth_pct']:.1f}%", f"{curr['miles']:,.0f} miles")
    f3.metric("Days in Motion",   f"{ff['days_moving']:.1f}", f"{curr['hours']:,.0f} hours total")

    st.divider()

    # --- Longest activities ---
    _render_longest_table(
        filtered, 'distance_miles',
        lambda r: f"{r['distance_miles']:,.1f} mi",
        "Longest Activities",
    )


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
# Data Explorer tab
# ---------------------------------------------------------------------------

def render_explore_tab(df, gear_map):
    """Interactive activity explorer — filter by date range, name search, and type."""
    all_types = sorted(df['final_type'].dropna().unique().tolist())
    min_date = df['start_date_local'].dt.date.min()
    max_date = df['start_date_local'].dt.date.max()

    c1, c2, c3 = st.columns([2, 2, 3])
    with c1:
        date_range = st.date_input(
            "Date range",
            value=(min_date, max_date),
            min_value=min_date,
            max_value=max_date,
            key="explore_date_range",
        )
    with c2:
        search_text = st.text_input(
            "Search activity name",
            value="",
            placeholder="e.g. morning ride",
            key="explore_search",
        )
    with c3:
        selected_types = st.multiselect(
            "Activity type",
            options=all_types,
            default=[],
            placeholder="All types",
            key="explore_types",
        )

    # --- Apply filters ---
    result = df.copy()

    if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
        start_d, end_d = date_range
        result = result[
            (result['start_date_local'].dt.date >= start_d) &
            (result['start_date_local'].dt.date <= end_d)
        ]

    if search_text.strip():
        result = result[result['name'].str.contains(search_text.strip(), case=False, na=False)]

    if selected_types:
        result = result[result['final_type'].isin(selected_types)]

    total_hours = result['moving_time'].sum() / 3600
    st.caption(
        f"{len(result):,} activities · {result['distance_miles'].sum():,.0f} mi · "
        f"{total_hours:,.0f} hrs"
    )

    if result.empty:
        st.info("No activities match the current filters.")
        return

    # --- Build display table ---
    display = result.sort_values('start_date_local', ascending=False).copy()
    display['Date']      = display['start_date_local'].apply(_fmt_date)
    display['Distance']  = display['distance_miles'].apply(lambda x: f"{x:,.1f} mi")
    display['Duration']  = display['moving_time'].apply(_fmt_time)
    display['Elevation'] = display['elevation_feet'].apply(lambda x: f"{x:,.0f} ft")
    display['Gear']      = display['gear_id'].apply(
        lambda g: gear_map.get(g, g) if g else "—"
    )

    show_cols  = ['Date', 'name', 'final_type', 'Distance', 'Duration', 'Elevation', 'Gear']
    rename_map = {'name': 'Activity', 'final_type': 'Type'}

    st.dataframe(
        display[show_cols].rename(columns=rename_map),
        use_container_width=True,
        hide_index=True,
    )

    st.download_button(
        "Download filtered results as CSV",
        _to_csv(display[show_cols].rename(columns=rename_map)),
        "strava_filtered_activities.csv",
        "text/csv",
        key="dl_explore_csv",
    )


# ---------------------------------------------------------------------------
# Export tab
# ---------------------------------------------------------------------------

def _fig_to_png(fig, width=1200, height=500):
    """Return PNG bytes for a Plotly figure (requires kaleido)."""
    return fig.to_image(format='png', width=width, height=height, scale=2)


def _to_csv(df):
    return df.to_csv(index=False).encode('utf-8')


def render_export_tab(df, settings):
    import calendar as _cal
    today = date.today()
    current_year = today.year
    current_season_key = today.year if today.month >= 10 else today.year - 1

    bike_df_all = df[df['final_type'].isin(BIKE_TYPES)].copy()
    ski_df_all  = df[df['final_type'].isin(SKI_TYPES)].copy()
    swim_df_all = df[df['final_type'].isin(SWIM_TYPES)].copy()

    # ── Section 1: Filtered activity summary ─────────────────────────────
    st.subheader("Activity Summary")
    period_options, period_meta = _build_period_options(df)
    c1, c2 = st.columns(2)
    with c1:
        selected_period = st.selectbox(
            "Time period", period_options, index=0, key="export_period"
        )
    with c2:
        selected_sport = st.selectbox(
            "Activities", _SPORT_OPTIONS, index=0, key="export_sport"
        )

    filtered = _filter_by_period(df, period_meta[selected_period])
    filtered = _filter_by_sport(filtered, selected_sport)

    if filtered.empty:
        st.info("No activities for this selection.")
        return

    stats   = process_data.compute_period_stats(filtered)
    curr    = stats['totals']
    yearly  = stats['yearly']
    monthly = stats['monthly']

    st.caption(
        f"{curr['activities']:,} activities · {curr['miles']:,.0f} mi · "
        f"{curr['hours']:,.0f} hrs · {curr['vert_ft']:,.0f} ft vert"
    )

    summary_figs = {
        'annual_distance': make_year_dist_chart(yearly, 'miles', 'Miles', current_year),
        'monthly_distance': make_labeled_bar_chart(
            monthly['month_label'], monthly['miles'],
            f"Monthly Distance — {selected_period}", "Month", "Miles",
        ),
        'sport_breakdown': make_sport_breakdown_chart(stats['sport_breakdown'], 'miles', 'Miles'),
    }

    col_l, col_r = st.columns(2)
    for i, (name, fig) in enumerate(summary_figs.items()):
        col = col_l if i % 2 == 0 else col_r
        with col:
            st.plotly_chart(fig, use_container_width=True, key=f"export_summary_{name}")
            st.download_button(
                f"Download {name}.png", _fig_to_png(fig), f"{name}.png", "image/png",
                key=f"dl_png_{name}",
            )

    st.divider()

    act_cols = {
        'start_date_local': 'Date', 'name': 'Activity', 'final_type': 'Type',
        'distance_miles': 'Miles', 'moving_time': 'Moving Time (s)',
        'elevation_feet': 'Elevation (ft)',
    }
    act_df = filtered[list(act_cols)].rename(columns=act_cols).copy()
    act_df['Date'] = pd.to_datetime(act_df['Date']).apply(_fmt_date)

    sport_df = (
        stats['sport_breakdown'][['final_type', 'activities', 'miles', 'hours', 'vert_ft']]
        .rename(columns={'final_type': 'Sport', 'activities': 'Activities',
                         'miles': 'Miles', 'hours': 'Hours', 'vert_ft': 'Vert (ft)'})
    )

    longest_df = (
        filtered.nlargest(20, 'distance_miles')
        [['start_date_local', 'name', 'final_type', 'distance_miles', 'moving_time']]
        .rename(columns={'start_date_local': 'Date', 'name': 'Activity', 'final_type': 'Type',
                         'distance_miles': 'Miles', 'moving_time': 'Moving Time (s)'})
        .copy()
    )
    longest_df['Date'] = pd.to_datetime(longest_df['Date']).apply(_fmt_date)

    tables = {
        'activities':         (act_df,     f"All {len(act_df):,} activities in selected period"),
        'sport_summary':      (sport_df,   "Distance and time by sport"),
        'longest_activities': (longest_df, "Top 20 activities by distance"),
    }

    st.subheader("Data Tables")
    for fname, (tdf, caption) in tables.items():
        st.caption(caption)
        st.dataframe(tdf.head(10), use_container_width=True, hide_index=True)
        st.download_button(
            f"Download {fname}.csv", _to_csv(tdf), f"{fname}.csv", "text/csv",
            key=f"dl_csv_{fname}",
        )

    st.divider()

    # ── Section 2: Annual sport summaries (full dataset) ─────────────────
    st.subheader("Annual Sport Summaries")
    st.caption("Full archive — not filtered by the period/sport selector above.")

    yearly_bike   = process_data.aggregate_by_year(bike_df_all)
    yearly_swim   = process_data.aggregate_swim_by_year(swim_df_all)
    seasonal_ski  = process_data.aggregate_ski_by_season(ski_df_all)
    equity_annual = process_data.aggregate_equity_by_year(df, settings)

    annual_figs = {}
    if not yearly_bike.empty:
        annual_figs['bike_annual_miles'] = make_year_dist_chart(
            yearly_bike, 'miles', 'Miles', current_year
        )
    if not equity_annual.empty:
        annual_figs['equity_annual'] = make_equity_annual_chart(equity_annual, current_year)
    if not yearly_swim.empty:
        # make_swim_year_chart expects [year, swims, <dist_col>] — trim to those three
        swim_plot = yearly_swim[['year', 'swims', 'meters']].copy()
        annual_figs['swim_annual_meters'] = make_swim_year_chart(swim_plot, current_year)
    if not seasonal_ski.empty:
        annual_figs['ski_seasonal_vert'] = make_season_vert_chart(
            seasonal_ski, current_season_key
        )

    ann_l, ann_r = st.columns(2)
    for i, (name, fig) in enumerate(annual_figs.items()):
        col = ann_l if i % 2 == 0 else ann_r
        with col:
            st.plotly_chart(fig, use_container_width=True, key=f"export_annual_{name}")
            st.download_button(
                f"Download {name}.png", _fig_to_png(fig), f"{name}.png", "image/png",
                key=f"dl_annual_png_{name}",
            )

    st.divider()

    # ── Section 3: Monthly breakdowns ────────────────────────────────────
    st.subheader("Monthly Breakdowns")
    available_years = sorted(df['year'].unique().tolist(), reverse=True)
    sel_year = st.selectbox("Year", available_years, key="export_monthly_year")

    def _bike_monthly_by_year(bdf, year):
        sub = bdf[bdf['year'] == year].copy()
        sub['month'] = sub['start_date_local'].dt.month
        agg = sub.groupby('month')['distance_miles'].sum()
        result = pd.DataFrame({'month': range(1, 13)})
        result['month_name'] = result['month'].apply(lambda m: _cal.month_abbr[m])
        result['miles'] = result['month'].map(agg).fillna(0)
        return result

    monthly_bike = _bike_monthly_by_year(bike_df_all, sel_year)
    monthly_swim = process_data.aggregate_swim_by_month(swim_df_all, sel_year)
    monthly_eq   = process_data.aggregate_equity_by_month(df, sel_year, settings)

    monthly_figs = {
        'bike_monthly_miles':  make_monthly_chart(monthly_bike, 'miles',   'Miles'),
        'swim_monthly_meters': make_monthly_chart(monthly_swim, 'meters',  'Meters'),
        'equity_monthly':      make_equity_monthly_chart(monthly_eq),
    }

    mo_l, mo_r = st.columns(2)
    for i, (name, fig) in enumerate(monthly_figs.items()):
        col = mo_l if i % 2 == 0 else mo_r
        with col:
            st.plotly_chart(fig, use_container_width=True, key=f"export_monthly_{name}")
            st.download_button(
                f"Download {name}.png", _fig_to_png(fig), f"{name}.png", "image/png",
                key=f"dl_monthly_png_{name}",
            )

    st.divider()

    # ── ZIP: all charts + tables ──────────────────────────────────────────
    st.subheader("Download Everything")
    slug = selected_period.replace(' ', '_').replace('/', '-')
    all_figs = {**summary_figs, **annual_figs, **monthly_figs}
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name, fig in all_figs.items():
            zf.writestr(f"{name}.png", _fig_to_png(fig))
        for fname, (tdf, _) in tables.items():
            zf.writestr(f"{fname}.csv", tdf.to_csv(index=False))
    zip_buf.seek(0)

    st.download_button(
        "Download all as ZIP",
        zip_buf,
        f"strava_export_{slug}.zip",
        "application/zip",
        key="dl_zip_all",
        type="primary",
    )


# ---------------------------------------------------------------------------
# Sync sidebar
# ---------------------------------------------------------------------------

def _load_last_sync():
    if os.path.exists(config.LAST_DATA_FILE):
        with open(config.LAST_DATA_FILE) as f:
            return json.load(f)
    return None


def _write_last_sync(total_count, new_count):
    data = {
        'last_timestamp': _datetime.now().timestamp(),
        'last_check': _datetime.now().isoformat(),
        'activity_count_latest_fetch': total_count,
        'new_on_last_sync': new_count,
    }
    with open(config.LAST_DATA_FILE, 'w') as f:
        json.dump(data, f, indent=2)


def _archive_count():
    if os.path.exists(config.ACTIVITIES_FILE):
        with open(config.ACTIVITIES_FILE) as f:
            return len(json.load(f))
    return 0


def _age_string(iso_str):
    """Return a human-readable age like '5 min ago', '3 hr ago', '2 days ago'."""
    try:
        dt = _datetime.fromisoformat(iso_str)
        secs = (_datetime.now() - dt).total_seconds()
        if secs < 120:
            return "just now"
        if secs < 3600:
            return f"{int(secs / 60)} min ago"
        if secs < 86400:
            return f"{int(secs / 3600)} hr ago"
        days = int(secs / 86400)
        return f"{days} day{'s' if days != 1 else ''} ago"
    except Exception:
        return iso_str[:10]


def _run_sync():
    """Execute the Strava sync inside a st.status widget (renders in caller's container)."""
    from src import fetch_data as _fd

    with st.status("Syncing Strava data…", expanded=True) as status:
        try:
            st.write("Refreshing access token…")
            token = _fd.get_access_token(
                config.TOKEN_FILE, config.CLIENT_ID, config.CLIENT_SECRET
            )

            years_str = ", ".join(str(y) for y in config.STRAVA_YEARS)
            st.write(f"Checking archive for {years_str}…")
            before = _archive_count()

            st.write("Fetching new activities from Strava…")
            _fd.maintain_archive(token, config.ACTIVITIES_FILE, config.STRAVA_YEARS)

            after = _archive_count()
            new_ct = max(after - before, 0)

            st.write("Saving sync record…")
            _write_last_sync(after, new_ct)

            if new_ct > 0:
                label = f"✅ {new_ct} new {'activity' if new_ct == 1 else 'activities'} added"
            else:
                label = "✅ Already up to date"

            status.update(label=label, state="complete", expanded=False)

            load_activities.clear()
            load_athlete_profile.clear()
            load_gear_map.clear()

            _time.sleep(1.5)
            st.rerun()

        except FileNotFoundError:
            status.update(label="❌ Token file not found", state="error")
            st.error(
                "Run `python run_pipeline.py` once from the terminal to complete "
                "the initial Strava OAuth flow and create the token file."
            )
        except ConnectionError as exc:
            status.update(label="❌ Strava API error", state="error")
            st.error(str(exc))
        except Exception as exc:
            status.update(label="❌ Sync failed", state="error")
            st.error(str(exc))


def render_sync_sidebar():
    with st.sidebar:
        st.header("Data Sync")

        last = _load_last_sync()
        if last:
            age = _age_string(last.get('last_check', ''))
            total = last.get('activity_count_latest_fetch', 0)
            new_ct = last.get('new_on_last_sync')

            st.caption(f"Last synced: **{age}**")
            st.metric("Activities in archive", f"{total:,}")
            if new_ct is not None:
                if new_ct > 0:
                    st.caption(f"↑ {new_ct} new on last sync")
                else:
                    st.caption("Up to date on last sync")
        else:
            st.caption("No sync record yet.")
            st.caption("Run `python run_pipeline.py` for first-time setup, then use Sync below.")

        st.divider()

        if st.button("🔄 Sync Now", type="primary", use_container_width=True):
            _run_sync()

        years_str = ", ".join(str(y) for y in config.STRAVA_YEARS)
        st.caption(f"Checking years: {years_str}")
        st.caption("Update `STRAVA_YEARS` in `.local.env` to change scope.")


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

render_sync_sidebar()

# TODO: restore tab_trends and "Trends" when work on the Trends tab continues
tab_combined, tab_bike, tab_snow, tab_swim, tab_wrapped, tab_explore, tab_export, tab_settings = st.tabs(
    ["Combined", "Bike", "Snow", "Swim", "Wrapped", "Explore", "Export", "Settings"]
)

with tab_combined:
    render_equity_tab(df, settings)

with tab_bike:
    render_bike_tab(bike_df, gear_map)

with tab_snow:
    render_ski_tab(ski_df, settings)

with tab_swim:
    render_swim_tab(swim_df, settings)

with tab_wrapped:
    render_wrapped_tab(df, settings, athlete_profile)

with tab_explore:
    render_explore_tab(df, gear_map)

with tab_export:
    render_export_tab(df, settings)

with tab_settings:
    render_settings_tab(settings)
