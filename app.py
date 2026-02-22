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
    make_period_comparison_chart,
    make_season_vert_chart,
    make_year_dist_chart,
    make_year_time_chart,
)
from src.config import BIKE_TYPES, GEAR_FALLBACKS, SKI_TYPES

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

if df.empty:
    st.error("No activity data found. Run the pipeline first.")
    st.stop()

bike_df = df[df['final_type'].isin(BIKE_TYPES)].copy()
ski_df = df[df['final_type'].isin(SKI_TYPES)].copy()

tab_bike, tab_ski, tab_swim, tab_equity, tab_settings = st.tabs(
    ["Bike", "Ski", "Swim", "Mile Equity", "Settings"]
)

with tab_bike:
    render_bike_tab(bike_df, gear_map)

with tab_ski:
    render_ski_tab(ski_df, settings)

with tab_swim:
    st.info("Swim tab — coming soon.")

with tab_equity:
    st.info("Mile Equity tab — coming soon.")

with tab_settings:
    render_settings_tab(settings)
