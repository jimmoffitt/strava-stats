"""
app.py — Streamlit dashboard entry point.

Renders the multi-tab Strava Stats UI: Bike, Snow, Swim, Combined (equity),
Wrapped, Explore, Export, and Settings. Each tab has a dedicated render_*
function that pulls pre-processed DataFrames from process_data, passes them
to Plotly figure factories in charts.py, and displays the results with
st.plotly_chart. Data is loaded once per session via @st.cache_data helpers;
the sidebar Sync Now button clears the cache and reruns after a live fetch.
"""
import io
import json
import os
import time as _time
import zipfile
from datetime import date, datetime as _datetime, timedelta, timezone as _timezone

import pandas as pd
import streamlit as st
import streamlit.components.v1 as _components

from src import config, process_data
from src import charts as _charts_mod
from src.charts import (
    make_bike_heatmap,
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
from src.config import BIKE_TYPES, EQUITY_SPORT_TYPES, GEAR_FALLBACKS, SKI_TYPES, SWIM_TYPES

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
            if section not in saved:
                continue
            if isinstance(settings[section], dict):
                settings[section].update(saved[section])
            else:
                settings[section] = saved[section]
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


def _decode_polyline(s: str) -> list:
    """Decode a Google Maps encoded polyline string to a list of (lat, lon) pairs."""
    coords, idx, lat, lng = [], 0, 0, 0
    while idx < len(s):
        for is_lat in (True, False):
            result, shift = 0, 0
            while True:
                b = ord(s[idx]) - 63
                idx += 1
                result |= (b & 0x1f) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if is_lat:
                lat += delta
            else:
                lng += delta
        coords.append((lat / 1e5, lng / 1e5))
    return coords


@st.cache_data
def load_bike_routes_all():
    """Decode polylines for every bike activity in the raw archive.

    Returns a list of dicts with keys:
      'dt'    — UTC-aware datetime
      'coords'— list of (lat, lon) tuples
    """
    with open(config.ACTIVITIES_FILE) as f:
        raw = json.load(f)

    routes = []
    for act in raw:
        if act.get('type') not in BIKE_TYPES and act.get('sport_type') not in BIKE_TYPES:
            continue
        poly = (act.get('map') or {}).get('summary_polyline', '')
        if not poly:
            continue
        coords = _decode_polyline(poly)
        if not coords:
            continue
        date_str = (act.get('start_date') or '').replace('Z', '+00:00')
        try:
            dt = _datetime.fromisoformat(date_str)
        except Exception:
            continue
        routes.append({'dt': dt, 'coords': coords})
    return routes


def _median_center(coord_lists: list):
    """Return (lat, lon) as the median of each route's start point.
    More robust than the mean — outlier rides don't drag the center away."""
    if not coord_lists:
        return 40.0, -105.0
    lats = sorted(r[0][0] for r in coord_lists)
    lons = sorted(r[0][1] for r in coord_lists)
    mid = len(lats) // 2
    if len(lats) % 2:
        return lats[mid], lons[mid]
    return (lats[mid - 1] + lats[mid]) / 2, (lons[mid - 1] + lons[mid]) / 2


# Convenience aliases so render functions read cleanly
_agg_swim_by_year        = process_data.aggregate_swim_by_year
_agg_swim_by_month       = process_data.aggregate_swim_by_month
_agg_ski_by_season       = process_data.aggregate_ski_by_season
_agg_ski_season_by_month = process_data.aggregate_ski_season_by_month

# ---------------------------------------------------------------------------
# ISO-week navigation helpers
# ---------------------------------------------------------------------------
def _fmt_date(dt):
    """Format a date or Timestamp as M/D/YYYY without leading zeros (cross-platform)."""
    return f"{dt.month}/{dt.day}/{dt.year}"


def _fmt_date_long(dt):
    """Format a date as 'Mon Jan 5, 2025' without leading zeros (cross-platform)."""
    return dt.strftime('%a %b ') + str(dt.day) + dt.strftime(', %Y')


def _stats_box(items):
    """Compact horizontal stats strip. items = list of (label, value) tuples."""
    dark = st.context.theme.type == 'dark'
    bg          = '#2a2d35' if dark else '#f7f7f7'
    label_color = '#9ca3af' if dark else '#888'
    value_color = '#e8e8e8' if dark else '#222'
    sep_color   = '#4b5563' if dark else '#ddd'

    parts = []
    for i, (label, value) in enumerate(items):
        sep = f'<span style="color:{sep_color};margin:0 14px;font-size:18px">|</span>' if i > 0 else ''
        parts.append(
            f'{sep}<span style="display:inline-block">'
            f'<span style="font-size:11px;color:{label_color};display:block;line-height:1.4">{label}</span>'
            f'<strong style="font-size:15px;color:{value_color}">{value}</strong>'
            f'</span>'
        )
    html = (
        f'<div style="display:flex;align-items:center;flex-wrap:wrap;gap:6px 0;'
        f'background:{bg};border-radius:6px;padding:10px 16px;margin:6px 0">'
        + ''.join(parts)
        + '</div>'
    )
    st.markdown(html, unsafe_allow_html=True)


def _apply_theme_js(theme: str) -> None:
    """Push a theme preference ('dark' or 'light') into Streamlit's localStorage
    and reload the parent page so Streamlit picks it up natively.  Only triggers
    a reload when the cached theme doesn't already match the requested one."""
    name = 'Dark' if theme == 'dark' else 'Light'
    js = f"""
    <script>
    (function() {{
        var key = 'stActiveTheme-' + window.parent.location.pathname + '-v1';
        var raw = window.parent.localStorage.getItem(key);
        var cur = null;
        try {{ cur = JSON.parse(raw).name; }} catch(e) {{}}
        if (cur !== '{name}') {{
            window.parent.localStorage.setItem(key, JSON.stringify({{name: '{name}'}}));
            window.parent.location.reload();
        }}
    }})();
    </script>
    """
    _components.html(js, height=0, scrolling=False)


def _active_tab_setter(name):
    """Returns an on_change callback that records which tab the user is on.
    Used with st.tabs(default=...) to survive explicit st.rerun() calls."""
    def _cb():
        st.session_state['active_tab'] = name
    return _cb


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
    # Annual distance is already shown as the thin overview chart at the top of the tab;
    # show the hours chart here so both dimensions are visible without duplication.
    st.plotly_chart(
        make_year_time_chart(yearly, current_year),
    )


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

    # Stats panels — shown before the chart
    ref_stats = process_data.get_period_stats(bike_df, ref_year, month=ref_month)
    prior_stats = process_data.get_period_stats(bike_df, ref_year - 1, month=ref_month)

    stat_cols = st.columns(3 if not is_current else 2)
    _render_stat_block(stat_cols[0], f"{calendar.month_name[ref_month]} {ref_year}", ref_stats, dist_col)
    _render_stat_block(stat_cols[1], f"{calendar.month_name[ref_month]} {ref_year - 1}", prior_stats, dist_col)
    if not is_current:
        shadow_stats = process_data.get_period_stats(bike_df, current_year, month=current_month)
        _render_stat_block(stat_cols[2], f"{calendar.month_name[current_month]} {current_year} (YTD)", shadow_stats, dist_col)

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
    st.plotly_chart(fig)


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

    # Stats panels — shown before the chart
    ref_stats = process_data.get_period_stats(bike_df, ref_iso_year, iso_week=ref_iso_week)
    prior_stats = process_data.get_period_stats(bike_df, ref_iso_year - 1, iso_week=ref_iso_week)

    stat_cols = st.columns(3 if not is_current else 2)
    _render_stat_block(stat_cols[0], _week_label(ref_iso_year, ref_iso_week), ref_stats, dist_col)
    _render_stat_block(stat_cols[1], _week_label(ref_iso_year - 1, ref_iso_week) + " (prior)", prior_stats, dist_col)
    if not is_current:
        shadow_stats = process_data.get_period_stats(bike_df, current_iso_year, iso_week=current_iso_week)
        _render_stat_block(stat_cols[2], "Current week (in progress)", shadow_stats, dist_col)

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
    st.plotly_chart(fig)


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
def render_bike_heatmap_view(compact: bool = False):
    """Geographic route heatmap — compact=True for the sidebar column embed."""
    frames = {
        'All time':     None,
        'This year':    365,
        'Last 90 days': 90,
        'Last 28 days': 28,
    }
    _cur = st.session_state.get('heatmap_frame')
    if _cur not in frames:
        st.session_state['heatmap_frame'] = 'All time'

    frame = st.selectbox(
        "Heatmap window", list(frames.keys()),
        key='heatmap_frame',
        label_visibility='collapsed' if compact else 'visible',
    )
    days = frames[frame]

    all_routes = load_bike_routes_all()

    if days is not None:
        cutoff = _datetime.now(_timezone.utc) - timedelta(days=days)
        routes = [r['coords'] for r in all_routes if r['dt'] >= cutoff]
    else:
        routes = [r['coords'] for r in all_routes]

    if not routes:
        st.info("No rides in the selected window.")
        return

    _home = load_settings().get('home_location', {})
    if _home.get('enabled') and _home.get('lat') is not None and _home.get('lon') is not None:
        center_lat, center_lon = float(_home['lat']), float(_home['lon'])
    else:
        center_lat, center_lon = _median_center(routes)
    height = 290 if compact else 560
    st.caption(f"{len(routes):,} rides")
    st.plotly_chart(
        make_bike_heatmap(routes, center_lat, center_lon, height=height),
    )


def render_bike_tab(bike_df, gear_map):
    current_year = date.today().year
    yearly_all = process_data.aggregate_by_year(bike_df)

    # --- Top row: annual distance chart (left) + static heatmap thumbnail (right) ---
    _chart_col, _thumb_col = st.columns([3, 1])
    with _chart_col:
        if not yearly_all.empty:
            _unit = st.session_state.get('bike_unit', 'Miles')
            _dc, _dl = ('miles', 'Miles') if _unit == 'Miles' else ('km', 'Km')
            st.plotly_chart(
                make_year_dist_chart(yearly_all, _dc, _dl, current_year, height=220),
            )
    with _thumb_col:
        _static_map_path = os.path.join(config.IMAGES_DIR, 'bike_heat_map_all_time.png')
        if os.path.exists(_static_map_path):
            st.image(_static_map_path, width="stretch")

    # --- Controls row ---
    ctrl_l, ctrl_r = st.columns(2)
    with ctrl_l:
        time_mode = st.radio(
            "Time mode", ["Year", "Month", "Week"],
            horizontal=True, key="bike_time_mode",
            on_change=_active_tab_setter("Bike"),
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

    yearly_all = process_data.aggregate_by_year(filtered_df)

    # --- Year mode: selector + two stats boxes + monthly chart ---
    if time_mode == "Year":
        available_years = sorted(filtered_df['year'].unique().tolist(), reverse=True) if not filtered_df.empty else []

        # Seed session state to avoid index=/key= conflict
        _cur_by = st.session_state.get('bike_year')
        if _cur_by not in available_years:
            st.session_state['bike_year'] = available_years[0] if available_years else None

        selected_year = st.selectbox("Year", available_years, key="bike_year")

        if selected_year is not None and not yearly_all.empty:
            yr_row = yearly_all[yearly_all['year'] == selected_year]
            max_ride = filtered_df[filtered_df['year'] == selected_year]['distance_miles'].max() \
                       if not filtered_df[filtered_df['year'] == selected_year].empty else 0

            if not yr_row.empty:
                r = yr_row.iloc[0]
                yr_dist = f"{r['miles']:,.0f} mi" if dist_col == 'miles' else f"{r['km']:,.0f} km"
                yr_max  = f"{max_ride:,.1f} mi" if dist_col == 'miles' else f"{max_ride * 1.60934:,.1f} km"
                _stats_box([
                    (f"{selected_year} Distance", yr_dist),
                    ("Longest Ride",              yr_max),
                    ("Hours",                     f"{r['hours']:,.0f} h"),
                    ("Rides",                     f"{int(r['count']):,}"),
                ])

            # All-time stats box
            all_max = filtered_df['distance_miles'].max() if not filtered_df.empty else 0
            all_avg = filtered_df['distance_miles'].mean() if not filtered_df.empty else 0
            all_dist = f"{yearly_all['miles'].sum():,.0f} mi" if dist_col == 'miles' \
                       else f"{yearly_all['km'].sum():,.0f} km"
            all_max_val = f"{all_max:,.1f} mi" if dist_col == 'miles' \
                          else f"{all_max * 1.60934:,.1f} km"
            all_avg_val = f"{all_avg:,.1f} mi" if dist_col == 'miles' \
                          else f"{all_avg * 1.60934:,.1f} km"
            _stats_box([
                ("All-Time Distance",    all_dist),
                ("All-Time Hours",       f"{yearly_all['hours'].sum():,.0f} h"),
                ("All-Time Rides",       f"{int(yearly_all['count'].sum()):,}"),
                ("Longest Ride (ever)",  all_max_val),
                ("Avg Ride Distance",    all_avg_val),
            ])

            # Distance by Month chart for selected year
            monthly_bike = process_data.aggregate_bike_by_month(filtered_df, selected_year)
            st.plotly_chart(
                make_monthly_chart(monthly_bike, dist_col, dist_label),
            )

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

    st.divider()
    render_bike_heatmap_view(compact=False)


# ---------------------------------------------------------------------------
# Ski tab
# ---------------------------------------------------------------------------
def render_ski_tab(ski_df, settings):
    if ski_df.empty:
        st.info("No snow activities found in the archive.")
        return

    goal_vert = settings['goals']['ski_season_vert_ft']
    conv = settings['conversions']
    ski_vert_per_mile = conv.get('ski_vert_per_ref_unit', conv.get('ski_vert_per_mile', 1000))
    seasons_cfg = settings.get('seasons', {})
    ski_start = seasons_cfg.get('ski_start_month', 11)
    ski_end   = seasons_cfg.get('ski_end_month', 5)

    today = date.today()
    current_season_key = today.year if today.month >= 10 else today.year - 1

    seasonal_df = _agg_ski_by_season(ski_df)

    # --- 1. Thin all-seasons overview chart ---
    st.plotly_chart(
        make_season_vert_chart(seasonal_df, current_season_key, goal_vert=goal_vert, height=220),
    )

    # --- 2. Season selector (e.g. "2025-2026") ---
    # season_key is the start year; season_label is the full "YYYY-YYYY" string.
    season_keys   = seasonal_df['season_key'].tolist()[::-1]
    season_labels = seasonal_df['season_label'].tolist()[::-1]
    label_to_key  = dict(zip(season_labels, season_keys))

    # Seed session state to avoid index= / key= conflict on re-render
    _cur_ski = st.session_state.get('ski_season_sel')
    if _cur_ski not in season_labels:
        default_label = next(
            (lbl for lbl, k in zip(season_labels, season_keys) if k == current_season_key),
            season_labels[0],
        )
        st.session_state['ski_season_sel'] = default_label

    selected_label = st.selectbox(
        "Season", season_labels, key="ski_season_sel",
        on_change=_active_tab_setter("Snow"),
    )
    selected_key = label_to_key[selected_label]

    # --- 3. Stats box ---
    row = seasonal_df[seasonal_df['season_key'] == selected_key].iloc[0]
    equity_miles = row['vert_ft'] / ski_vert_per_mile if ski_vert_per_mile > 0 else 0
    _stats_box([
        ("Days on snow",   str(int(row['days']))),
        ("Sessions",       str(int(row['sessions']))),
        ("Total vert",     f"{row['vert_ft']:,.0f} ft"),
        ("Max day",        f"{row['max_vert_day']:,.0f} ft"),
        ("Avg vert / day", f"{row['avg_vert_day']:,.0f} ft"),
        ("Equity miles",   f"{equity_miles:,.0f} mi"),
    ])
    if goal_vert > 0:
        progress = min(row['vert_ft'] / goal_vert, 1.0)
        st.progress(progress, text=f"Season goal: {row['vert_ft']:,.0f} / {goal_vert:,.0f} ft ({progress*100:.0f}%)")

    # All-time stats box
    best_season_row = seasonal_df.loc[seasonal_df['vert_ft'].idxmax()]
    all_vert      = seasonal_df['vert_ft'].sum()
    all_days      = int(seasonal_df['days'].sum())
    all_sessions  = int(seasonal_df['sessions'].sum())
    all_seasons   = len(seasonal_df)
    all_eq_miles  = all_vert / ski_vert_per_mile if ski_vert_per_mile > 0 else 0
    _stats_box([
        ("All-Time Seasons",   str(all_seasons)),
        ("All-Time Vert",      f"{all_vert:,.0f} ft"),
        ("All-Time Days",      str(all_days)),
        ("All-Time Sessions",  str(all_sessions)),
        ("Best Season",        f"{best_season_row['season_label']}  {best_season_row['vert_ft']:,.0f} ft"),
        ("All-Time Eq Miles",  f"{all_eq_miles:,.0f} mi"),
    ])

    # --- 4. Vert by Month chart (season months, spanning both calendar years) ---
    monthly_season = _agg_ski_season_by_month(ski_df, selected_key, ski_start, ski_end)
    if not monthly_season.empty:
        st.plotly_chart(
            make_monthly_chart(monthly_season, 'vert_ft', 'ft'),
        )

    # --- 5. Most Recent Snow Activities ---
    st.divider()
    _render_recent_table(
        ski_df,
        lambda r: f"{r['elevation_feet']:,.0f} ft vert",
        "Most Recent Snow Activities",
        key_prefix="ski",
    )

    # --- 6. Biggest Snow Days (All Seasons) ---
    st.divider()
    _render_longest_table(
        ski_df, 'elevation_feet',
        lambda r: f"{r['elevation_feet']:,.0f} ft",
        "Biggest Snow Days (All Seasons)",
    )

    # --- 7. All Snow Days (reverse chronological) ---
    st.divider()
    st.subheader("Snow Days")
    all_days = process_data.get_ski_days_table(ski_df)
    if all_days.empty:
        st.info("No snow days found.")
        return

    display = all_days.copy()
    display['Date']      = pd.to_datetime(display['date']).apply(_fmt_date_long)
    display['Season']    = display['season_label']
    display['Activity']  = display['activity']
    display['Type']      = display['type']
    display['Vert (ft)'] = display['vert_ft'].apply(lambda x: f"{x:,.0f}")
    display['Dist (mi)'] = display['distance_mi'].apply(lambda x: f"{x:.1f}")
    display['Hours']     = display['hours'].apply(lambda x: f"{x:.1f}")
    st.dataframe(
        display[['Date', 'Season', 'Activity', 'Type', 'Vert (ft)', 'Dist (mi)', 'Hours']],
        hide_index=True,
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


def render_swim_tab(swim_df, settings, df=None):
    if swim_df.empty:
        st.info("No swim activities found in the archive.")
        return

    monthly_goal_m = settings['goals']['swim_monthly_meters']
    seasons = settings.get('seasons', {})
    swim_start = seasons.get('swim_start_month', 5)
    swim_end   = seasons.get('swim_end_month', 9)
    current_year = date.today().year

    # --- 1. Thin multi-year overview chart ---
    yearly = _agg_swim_by_year(swim_df)
    if not yearly.empty:
        _unit = st.session_state.get('swim_unit', 'Meters')
        _dc = 'meters' if _unit == 'Meters' else 'yards'
        yearly_plot = yearly.rename(columns={_dc: '_dist'})[['year', 'swims', '_dist']].copy()
        yearly_plot.columns = ['year', 'swims', _dc]
        st.plotly_chart(
            make_swim_year_chart(yearly_plot, current_year, height=220),
        )

    # --- 2. Controls ---
    ctrl_l, ctrl_r = st.columns(2)
    with ctrl_l:
        years = sorted(swim_df['year'].unique().tolist(), reverse=True)
        selected_year = st.selectbox("Year", years, key="swim_year",
                                     on_change=_active_tab_setter("Swim"))
    with ctrl_r:
        unit = st.radio("Units", ["Meters", "Yards"], horizontal=True, key="swim_unit")

    dist_col   = 'meters' if unit == 'Meters' else 'yards'
    dist_label = 'm' if unit == 'Meters' else 'yd'
    mult       = 1.0 if unit == 'Meters' else 1.09361
    goal_val   = monthly_goal_m * mult

    monthly_all = _agg_swim_by_month(swim_df, selected_year)
    if swim_start <= swim_end:
        swim_months = list(range(swim_start, swim_end + 1))
    else:
        swim_months = list(range(swim_start, 13)) + list(range(1, swim_end + 1))
    _swim_order = {m: i for i, m in enumerate(swim_months)}
    monthly = (
        monthly_all[monthly_all['month'].isin(swim_months)]
        .copy()
        .assign(_order=lambda d: d['month'].map(_swim_order))
        .sort_values('_order')
        .drop(columns='_order')
    )

    # --- 3. Stats bar (under controls, before charts) ---
    year_row = yearly[yearly['year'] == selected_year]
    if not year_row.empty:
        row = year_row.iloc[0]
        total_dist = row[dist_col]
        total_swims = int(row['swims'])
        avg_dist = row['avg_meters'] * mult
        months_with_data = int((monthly[dist_col] > 0).sum())
        avg_monthly = total_dist / months_with_data if months_with_data else 0

        max_swim = swim_df['distance'].max() * mult if not swim_df.empty else 0
        _stats_box([
            ("Total Distance", f"{total_dist:,.0f} {dist_label}"),
            ("Swims",          str(total_swims)),
            ("Longest Swim",   f"{max_swim:,.0f} {dist_label}"),
            ("Avg per Swim",   f"{avg_dist:,.0f} {dist_label}"),
            ("Avg per Month",  f"{avg_monthly:,.0f} {dist_label}"),
        ])
        if goal_val > 0:
            progress = min(avg_monthly / goal_val, 1.0)
            st.progress(progress,
                text=f"Monthly goal pace: {avg_monthly:,.0f} / {goal_val:,.0f} {dist_label} avg ({progress*100:.0f}%)")

        # All-time stats box
        all_dist_m   = yearly['meters'].sum()
        all_swims    = int(yearly['swims'].sum())
        best_yr_row  = yearly.loc[yearly['meters'].idxmax()]
        all_max_swim = swim_df['distance'].max() * mult if not swim_df.empty else 0
        all_avg_swim = (all_dist_m / all_swims * mult) if all_swims else 0
        _stats_box([
            ("All-Time Distance", f"{all_dist_m * mult:,.0f} {dist_label}"),
            ("All-Time Swims",    str(all_swims)),
            ("Best Year",         f"{int(best_yr_row['year'])}  {best_yr_row['meters'] * mult:,.0f} {dist_label}"),
            ("Longest Swim",      f"{all_max_swim:,.0f} {dist_label}"),
            ("Avg per Swim",      f"{all_avg_swim:,.0f} {dist_label}"),
        ])

    # --- 4. Monthly chart ---
    st.plotly_chart(
        make_monthly_chart(monthly, dist_col, dist_label, goal=goal_val),
    )

    fmt_swim = (lambda r: f"{r['distance']:,.0f} m") if unit == 'Meters' else (lambda r: f"{r['distance'] * 1.09361:,.0f} yd")

    # --- 5. Most Recent Swims ---
    st.divider()
    _render_recent_table(swim_df, fmt_swim, "Most Recent Swims", key_prefix="swim")

    # --- 6. Longest Swims ---
    st.divider()
    _render_longest_table(swim_df, 'distance', fmt_swim, "Longest Swims")



# ---------------------------------------------------------------------------
# Mile Equity tab
# ---------------------------------------------------------------------------
def render_equity_tab(df, settings):
    goals       = settings.get('goals', {})
    annual_goal = goals.get('annual_equity_miles', 0)
    monthly_goal= goals.get('monthly_equity_miles', 0)
    ref_label   = settings.get('reference_sport', 'Bike')

    today = date.today()
    current_year = today.year
    yearly = process_data.aggregate_equity_by_year(df, settings)

    # --- 1. Thin, wide multi-year overview chart ---
    st.plotly_chart(
        make_equity_annual_chart(yearly, current_year, ref_label=ref_label, height=220),
    )

    # --- 2. Year selector ---
    available_years = sorted(df['year'].unique().tolist(), reverse=True)
    default_year = today.year - 1
    default_idx = available_years.index(default_year) if default_year in available_years else 0
    selected_year = st.selectbox("Year", available_years, index=default_idx, key="equity_year",
                                 on_change=_active_tab_setter("Combined"))

    # --- 3. Compact stats box + goal ---
    yr_row = yearly[yearly['year'] == selected_year]
    if yr_row.empty:
        st.info("No data for the selected year.")
        return

    row = yr_row.iloc[0]
    total_eq = row['total']
    pct = lambda v: f" ({v / total_eq * 100:.0f}%)" if total_eq else ""

    sport_items = [("Total Equity Miles", f"{total_eq:,.0f}")]
    for col, label in [('bike','Bike'), ('run','Run'), ('ski','Ski'), ('swim','Swim'),
                       ('hike','Hike'), ('paddle','Paddle'), ('custom','Custom')]:
        if col in yr_row.columns:
            val = row[col]
            if val > 0.5:
                sport_items.append((label, f"{val:,.0f} mi{pct(val)}"))
    _stats_box(sport_items)

    # All-time stats box
    all_total = yearly['total'].sum()
    all_items = [("All-Time Equity Miles", f"{all_total:,.0f}")]
    for col, label in [('bike','Bike'), ('run','Run'), ('ski','Ski'), ('swim','Swim'),
                       ('hike','Hike'), ('paddle','Paddle'), ('custom','Custom')]:
        if col in yearly.columns:
            all_val = yearly[col].sum()
            if all_val > 0.5:
                all_pct = f" ({all_val / all_total * 100:.0f}%)" if all_total else ""
                all_items.append((label, f"{all_val:,.0f} mi{all_pct}"))
    _stats_box(all_items)

    if annual_goal:
        prog = min(total_eq / annual_goal, 1.0)
        st.caption(f"Annual goal: {total_eq:,.0f} / {annual_goal:,} equity miles ({prog * 100:.0f}%)")
        st.progress(prog)

    conv = settings.get('conversions', {})
    swim_r = conv.get('swim_meters_per_ref_unit', 100)
    ski_r  = conv.get('ski_vert_per_ref_unit', 1000)
    st.caption(
        f"Conversion rates — {ref_label}: 1 mi = 1 equity mi  ·  "
        f"Swim: {swim_r:,.0f} m = 1 equity mi  ·  "
        f"Ski: {ski_r:,.0f} vert ft = 1 equity mi"
    )

    # --- 4. Monthly breakdown ---
    monthly = process_data.aggregate_equity_by_month(df, selected_year, settings)
    st.plotly_chart(
        make_equity_monthly_chart(monthly, ref_label=ref_label, goal=monthly_goal),
    )

    st.divider()

    # --- Custom Equity activities table ---
    st.subheader("Custom Equity Activities")
    st.caption(
        "Activities with a custom equity marker (e.g. GEq gardening) whose sport has no Strava "
        "type are listed here. They count toward the 'Custom' slice in the chart above."
    )

    eq_df = process_data.get_eq_activities(df)
    custom_eq = eq_df[~eq_df['final_type'].isin(EQUITY_SPORT_TYPES)]
    if custom_eq.empty:
        st.info("No custom equity activities found.")
        return

    year_options = ["All years"] + [str(y) for y in sorted(custom_eq['year'].unique(), reverse=True)]
    eq_year_sel = st.selectbox("Filter by year", year_options, key="eq_year_filter")
    if eq_year_sel != "All years":
        show_eq = custom_eq[custom_eq['year'] == int(eq_year_sel)].copy()
    else:
        show_eq = custom_eq.copy()

    display = show_eq.copy()
    display['Date']     = pd.to_datetime(display['date']).apply(_fmt_date)
    display['Activity'] = display['name']
    display['Type']     = display['final_type']
    display['Miles']    = display['miles'].apply(lambda v: f"{v:,.1f}")
    st.dataframe(
        display[['Date', 'Activity', 'Type', 'Miles']],
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
    st.dataframe(recent[src_cols].rename(columns=hdr_map), width="stretch", hide_index=True)


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
    st.dataframe(top[src_cols].rename(columns=hdr_map), width="stretch", hide_index=True)


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
            "Time period", period_options, index=0, key="wrapped_period",
            on_change=_active_tab_setter("Wrapped"),
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
            )
        else:
            monthly = stats['monthly']
            st.plotly_chart(
                make_labeled_bar_chart(
                    monthly['month_label'], monthly['miles'],
                    "Monthly Distance", "Month", "Miles",
                ),
            )
    with col_r:
        st.plotly_chart(
            make_sport_breakdown_chart(stats['sport_breakdown'], 'miles', 'Miles'),
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
            on_change=_active_tab_setter("Tools"),
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
            st.plotly_chart(fig, key=f"export_summary_{name}")
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
        st.dataframe(tdf.head(10), width="stretch", hide_index=True)
        st.download_button(
            f"Download {fname}.csv", _to_csv(tdf), f"{fname}.csv", "text/csv",
            key=f"dl_csv_{fname}",
        )

    st.divider()

    # ── Section 2: Annual sport summaries (full dataset) ─────────────────
    st.subheader("Annual Sport Summaries")
    st.caption("Full archive — not filtered by the period/sport selector above.")

    yearly_bike   = process_data.aggregate_by_year(bike_df_all)
    yearly_swim   = _agg_swim_by_year(swim_df_all)
    seasonal_ski  = _agg_ski_by_season(ski_df_all)
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
            st.plotly_chart(fig, key=f"export_annual_{name}")
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
    monthly_swim = _agg_swim_by_month(swim_df_all, sel_year)
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
            st.plotly_chart(fig, key=f"export_monthly_{name}")
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
        _charts_mod.set_theme(st.context.theme.type == 'dark')

        st.divider()
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

        if st.button("🔄 Sync Now", type="primary", width="stretch"):
            _run_sync()

        years_str = ", ".join(str(y) for y in config.STRAVA_YEARS)
        st.caption(f"Checking years: {years_str}")
        st.caption("Update `STRAVA_YEARS` in `.local.env` to change scope.")


# ---------------------------------------------------------------------------
# Settings tab
# ---------------------------------------------------------------------------
def render_settings_tab(settings):
    conv         = settings.get('conversions', {})
    goals        = settings.get('goals', {})
    saved_seasons = settings.get('seasons', {})
    saved_home   = settings.get('home_location', {})

    # --- Seed all session-state keys upfront ---
    # Must happen before sub-tabs render so the Save button can read from
    # st.session_state even when a sub-tab's widgets are not currently visible.
    ref_options = ["Bike", "Run", "Hike"]

    if st.session_state.get('settings_theme') not in ['dark', 'light']:
        st.session_state['settings_theme'] = settings.get('theme', 'dark')

    _cur_ref = st.session_state.get('settings_ref_sport')
    if _cur_ref not in ref_options:
        _saved_ref = settings.get('reference_sport', 'Bike')
        st.session_state['settings_ref_sport'] = _saved_ref if _saved_ref in ref_options else 'Bike'

    for _k, _v in [
        ('settings_bike_rate',    float(conv.get('bike_miles_per_ref_unit', 1.0))),
        ('settings_run_rate',     float(conv.get('run_miles_per_ref_unit', 1.0))),
        ('settings_hike_rate',    float(conv.get('hike_miles_per_ref_unit', 3.0))),
        ('settings_paddle_rate',  float(conv.get('paddle_miles_per_ref_unit', 2.0))),
        ('settings_swim_rate',    int(conv.get('swim_meters_per_ref_unit', conv.get('swim_meters_per_mile', 100)))),
        ('settings_ski_rate',     int(conv.get('ski_vert_per_ref_unit', conv.get('ski_vert_per_mile', 1000)))),
        ('settings_annual_eq',    goals.get('annual_equity_miles', 3000)),
        ('settings_monthly_eq',   goals.get('monthly_equity_miles', 250)),
        ('settings_ski_goal',     goals.get('ski_season_vert_ft', 200000)),
        ('settings_swim_goal',    goals.get('swim_monthly_meters', 10000)),
        ('settings_home_enabled', bool(saved_home.get('enabled', False))),
        ('settings_home_lat',     float(saved_home['lat']) if saved_home.get('lat') is not None else 40.0),
        ('settings_home_lon',     float(saved_home['lon']) if saved_home.get('lon') is not None else -105.0),
    ]:
        if _k not in st.session_state:
            st.session_state[_k] = _v

    _month_nums  = list(range(1, 13))
    for _k, _field, _default in [
        ('settings_ski_start_month',  'ski_start_month',  11),
        ('settings_ski_end_month',    'ski_end_month',     5),
        ('settings_swim_start_month', 'swim_start_month',  5),
        ('settings_swim_end_month',   'swim_end_month',    9),
    ]:
        if _k not in st.session_state:
            st.session_state[_k] = saved_seasons.get(_field, _default)

    # ---- Sub-tabs as navigation TOC ----
    stab_appear, stab_sports, stab_goals, stab_seasons, stab_map = st.tabs(
        ["Appearance", "Sports", "Goals", "Seasons", "Map"]
    )

    # ---- Appearance ----
    with stab_appear:
        st.subheader("Theme")
        st.radio(
            "Appearance", ['dark', 'light'],
            horizontal=True,
            format_func=lambda x: 'Dark' if x == 'dark' else 'Light',
            key='settings_theme',
        )
        st.caption("Takes effect immediately on save.")

    # ---- Sports ----
    with stab_sports:
        st.subheader("Reference Sport")
        st.caption(
            "Equity miles are expressed in units of this sport. "
            "Bike, Run, and Hike all use distance (miles) so they can serve as the "
            "reference. Swim and Ski use different native units (meters / vertical "
            "feet) — their conversion rates below always express how many native "
            "units equal one equity mile."
        )
        st.radio(
            "Reference sport", ref_options,
            horizontal=True,
            key="settings_ref_sport",
        )
        _active_ref = st.session_state.get('settings_ref_sport', 'Bike')
        _saved_ref  = settings.get('reference_sport', 'Bike')
        if _active_ref != _saved_ref:
            st.warning(
                f"Reference sport changed from **{_saved_ref}** to **{_active_ref}**. "
                "Review the conversion rates below to make sure they still reflect "
                "your effort equivalences, then hit Save."
            )

        st.divider()
        st.subheader("Equity Mile Conversions")
        st.caption(f"How many native units = 1 {_active_ref} equity mile.")

        dcol_bike, dcol_run, dcol_hike, dcol_paddle = st.columns(4)
        with dcol_bike:
            st.markdown("**Bike**")
            if _active_ref == 'Bike':
                st.caption("Reference")
                st.markdown("1 mi = 1 equity mi")
                st.session_state['settings_bike_rate'] = 1.0
            else:
                st.number_input(
                    "miles = 1 equity mi", min_value=0.1, max_value=100.0,
                    step=0.5, format="%.1f", key="settings_bike_rate",
                )
        with dcol_run:
            st.markdown("**Run**")
            if _active_ref == 'Run':
                st.caption("Reference")
                st.markdown("1 mi = 1 equity mi")
                st.session_state['settings_run_rate'] = 1.0
            else:
                st.number_input(
                    "miles = 1 equity mi", min_value=0.1, max_value=100.0,
                    step=0.5, format="%.1f", key="settings_run_rate",
                )
        with dcol_hike:
            st.markdown("**Hike / Walk**")
            if _active_ref == 'Hike':
                st.caption("Reference")
                st.markdown("1 mi = 1 equity mi")
                st.session_state['settings_hike_rate'] = 1.0
            else:
                st.number_input(
                    "miles = 1 equity mi", min_value=0.1, max_value=100.0,
                    step=0.5, format="%.1f", key="settings_hike_rate",
                )
        with dcol_paddle:
            st.markdown("**Paddle**")
            st.number_input(
                "miles = 1 equity mi", min_value=0.1, max_value=100.0,
                step=0.5, format="%.1f", key="settings_paddle_rate",
            )

        scol_swim, scol_ski = st.columns(2)
        with scol_swim:
            st.markdown("**Swim**")
            st.number_input(
                "meters = 1 equity mi",
                min_value=1, max_value=10000, step=10,
                key="settings_swim_rate",
            )
        with scol_ski:
            st.markdown("**Ski**")
            st.number_input(
                "vertical feet = 1 equity mi",
                min_value=100, max_value=100000, step=100,
                key="settings_ski_rate",
            )

    # ---- Goals ----
    with stab_goals:
        st.subheader("Goals")
        st.markdown("**Equity Miles**")
        gcol_annual, gcol_monthly = st.columns(2)
        with gcol_annual:
            st.number_input(
                "Annual equity miles",
                min_value=0, max_value=100000, step=100,
                key="settings_annual_eq",
            )
        with gcol_monthly:
            st.number_input(
                "Monthly equity miles",
                min_value=0, max_value=10000, step=10,
                key="settings_monthly_eq",
            )

        st.markdown("**Sport-Specific**")
        gcol_ski, gcol_swim = st.columns(2)
        with gcol_ski:
            st.number_input(
                "Ski season vertical feet (cumulative)",
                min_value=0, max_value=10000000, step=10000,
                key="settings_ski_goal",
            )
        with gcol_swim:
            st.number_input(
                "Swim monthly meters",
                min_value=0, max_value=1000000, step=500,
                key="settings_swim_goal",
            )

    # ---- Seasons ----
    with stab_seasons:
        st.subheader("Season Months")
        st.caption("Controls which months appear in the monthly chart on each sport tab.")

        _month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                        'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

        smcol_ski, smcol_swim = st.columns(2)
        with smcol_ski:
            st.markdown("**Ski season**")
            st.selectbox(
                "Start month", _month_nums,
                format_func=lambda m: _month_names[m - 1],
                key="settings_ski_start_month",
            )
            st.selectbox(
                "End month", _month_nums,
                format_func=lambda m: _month_names[m - 1],
                key="settings_ski_end_month",
            )
        with smcol_swim:
            st.markdown("**Swim season**")
            st.selectbox(
                "Start month", _month_nums,
                format_func=lambda m: _month_names[m - 1],
                key="settings_swim_start_month",
            )
            st.selectbox(
                "End month", _month_nums,
                format_func=lambda m: _month_names[m - 1],
                key="settings_swim_end_month",
            )

    # ---- Map ----
    with stab_map:
        st.subheader("Home Location")
        st.caption(
            "When enabled, the bike heatmap centers on your home location instead of "
            "the median of your ride start points."
        )
        st.checkbox("Use custom home location for heatmap", key="settings_home_enabled")
        if st.session_state.get('settings_home_enabled'):
            hm_col_lat, hm_col_lon = st.columns(2)
            with hm_col_lat:
                st.number_input(
                    "Latitude", min_value=-90.0, max_value=90.0,
                    step=0.0001, format="%.6f",
                    key="settings_home_lat",
                )
            with hm_col_lon:
                st.number_input(
                    "Longitude", min_value=-180.0, max_value=180.0,
                    step=0.0001, format="%.6f",
                    key="settings_home_lon",
                )
            st.caption("Tip: right-click any location in Google Maps → 'What's here?' to copy coordinates.")

    # ---- Save (outside sub-tabs) ----
    st.divider()
    if st.button("Save settings", type="primary"):
        _new_theme  = st.session_state.get('settings_theme', 'dark')
        _old_theme  = settings.get('theme', 'dark')
        _ref_sport  = st.session_state.get('settings_ref_sport', 'Bike')
        _home_enabled = st.session_state.get('settings_home_enabled', False)
        _home_lat   = st.session_state.get('settings_home_lat', None)
        _home_lon   = st.session_state.get('settings_home_lon', None)

        updated = {
            'theme': _new_theme,
            'reference_sport': _ref_sport,
            'conversions': {
                'bike_miles_per_ref_unit':   st.session_state.get('settings_bike_rate', 1.0),
                'run_miles_per_ref_unit':    st.session_state.get('settings_run_rate', 1.0),
                'hike_miles_per_ref_unit':   st.session_state.get('settings_hike_rate', 3.0),
                'paddle_miles_per_ref_unit': st.session_state.get('settings_paddle_rate', 2.0),
                'swim_meters_per_ref_unit':  st.session_state.get('settings_swim_rate', 100),
                'ski_vert_per_ref_unit':     st.session_state.get('settings_ski_rate', 1000),
            },
            'goals': {
                'annual_equity_miles':  st.session_state.get('settings_annual_eq', 3000),
                'monthly_equity_miles': st.session_state.get('settings_monthly_eq', 250),
                'ski_season_vert_ft':   st.session_state.get('settings_ski_goal', 200000),
                'swim_monthly_meters':  st.session_state.get('settings_swim_goal', 10000),
            },
            'seasons': {
                'ski_start_month':  st.session_state.get('settings_ski_start_month', 11),
                'ski_end_month':    st.session_state.get('settings_ski_end_month', 5),
                'swim_start_month': st.session_state.get('settings_swim_start_month', 5),
                'swim_end_month':   st.session_state.get('settings_swim_end_month', 9),
            },
            'home_location': {
                'enabled': _home_enabled,
                'lat': _home_lat if _home_enabled else None,
                'lon': _home_lon if _home_enabled else None,
            },
        }
        with open(config.SETTINGS_FILE, 'w') as f:
            json.dump(updated, f, indent=2)
        load_settings.clear()
        if _new_theme != _old_theme:
            _apply_theme_js(_new_theme)
        else:
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

# Eq-named activities are equity declarations — exclude them from sport tabs
# so their declared distances don't corrupt actual swim/ski metrics.
# Bike is kept whole because SBEq entries are real indoor rides.
_eq_mask = df['name'].str.match(process_data._EQ_PATTERN, na=False)
bike_df  = df[df['final_type'].isin(BIKE_TYPES)].copy()
ski_df   = df[df['final_type'].isin(SKI_TYPES)  & ~_eq_mask].copy()
swim_df  = df[df['final_type'].isin(SWIM_TYPES) & ~_eq_mask].copy()

render_sync_sidebar()

# On first render of each browser session, sync localStorage to the saved theme
# preference.  Subsequent renders skip this (initial_theme_synced is set) so
# that the user can still override via Streamlit's native ⋮ Settings menu.
if not st.session_state.get('initial_theme_synced'):
    st.session_state['initial_theme_synced'] = True
    _saved_theme = settings.get('theme', 'dark')
    _current_theme = st.context.theme.type or 'dark'
    if _saved_theme != _current_theme:
        _apply_theme_js(_saved_theme)

# TODO: restore tab_trends and "Trends" when work on the Trends tab continues
_TAB_NAMES = ["Bike", "Snow", "Swim", "Combined", "Wrapped", "Settings", "Tools"]
_default_tab = st.session_state.get('active_tab')
tab_bike, tab_snow, tab_swim, tab_combined, tab_wrapped, tab_settings, tab_tools = st.tabs(
    _TAB_NAMES,
    default=_default_tab if _default_tab in _TAB_NAMES else None,
)

with tab_bike:
    render_bike_tab(bike_df, gear_map)

with tab_snow:
    render_ski_tab(ski_df, settings)

with tab_swim:
    render_swim_tab(swim_df, settings, df)

with tab_combined:
    render_equity_tab(df, settings)

with tab_wrapped:
    render_wrapped_tab(df, settings, athlete_profile)

with tab_settings:
    render_settings_tab(settings)

with tab_tools:
    tools_explore, tools_export = st.tabs(["Explore", "Export"])
    with tools_explore:
        render_explore_tab(df, gear_map)
    with tools_export:
        render_export_tab(df, settings)
