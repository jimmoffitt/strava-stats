"""
app.py — Streamlit dashboard entry point.

Renders the multi-tab Equity Miles UI: Bike, Snow, Swim, Combined (equity),
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
    HIKE_GREEN,
    HIKE_GREEN_LIGHT,
    RUN_PURPLE,
    RUN_PURPLE_LIGHT,
    SKI_BLUE,
    SKI_BLUE_LIGHT,
    STRAVA_ORANGE,
    SWIM_TEAL,
    SWIM_TEAL_LIGHT,
    make_bike_heatmap,
    make_calendar_heatmap,
    make_equity_annual_chart,
    make_equity_monthly_chart,
    make_labeled_bar_chart,
    make_monthly_chart,
    make_period_comparison_chart,
    make_recent_months_chart,
    make_season_vert_chart,
    make_sport_breakdown_chart,
    make_sport_breakdown_donut,
    make_swim_year_chart,
    make_year_dist_chart,
    make_year_time_chart,
)
from src.config import BIKE_TYPES, GEAR_FALLBACKS, HIKE_TYPES, RUN_TYPES, SKI_TYPES, SWIM_TYPES

# ---------------------------------------------------------------------------
# Page config (must be first Streamlit call)
# ---------------------------------------------------------------------------
st.set_page_config(layout="wide", page_title="Equity Miles")


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
    """Load athlete_profile.json (id, name, follower counts) written by
    run_pipeline.py / the Sync Now button — empty dict if never synced."""
    if os.path.exists(config.ATHLETE_PROFILE_FILE):
        with open(config.ATHLETE_PROFILE_FILE) as f:
            return json.load(f)
    return {}


@st.cache_data
def load_athlete_stats():
    """Load athlete_stats.json (Strava's own all-time/YTD/recent totals from
    /athletes/{id}/stats) — empty dict if never synced."""
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
    """Decode a Google Maps encoded polyline string to a list of (lat, lon)
    pairs. Duplicated (not shared) in publish_data.py's own _decode_polyline
    — that's a src/ module used standalone by the run_pipeline.py CLI, so it
    can't import from this top-level app.py without a circular/backwards
    dependency (and would drag in Streamlit as an unwanted CLI dependency)."""
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
# Shared render/formatting helpers, used across multiple tabs
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


def _all_time_line(*, distance, hours, activities, seasons, best_year,
                   largest_month, highest, equity, avg, avg_time, avg_speed):
    """Render the uniform all-time stats line shared by the sport tabs.

    Every sport shows the same slots in the same order; values are pre-formatted
    display strings so each tab can express them in its own native units (bike
    miles, snow vertical feet, swim meters, …). The "biggest" stats run from
    coarse to fine: best year → largest month → highest single activity. The
    trailing averages (distance, time, speed) are all per-activity.
    """
    _stats_box([
        ("All-Time Distance", distance),
        ("Hours",             hours),
        ("Activities",        activities),
        ("Seasons",           seasons),
        ("Best Year",         best_year),
        ("Largest Month",     largest_month),
        ("Highest Distance",  highest),
        ("Equity Miles",      equity),
        ("Avg Distance",      avg),
        ("Avg Time",          avg_time),
        ("Avg Speed",         avg_speed),
    ])


def _chart_header_with_goal(title, current=None, goal=None, unit_label="", color=STRAVA_ORANGE):
    """Section header row: bold title on the left, a compact 'current / goal
    (pct%)' readout + thin progress bar on the right — meant to sit directly
    above the chart it describes, so the goal reads as part of the chart's
    header rather than a disconnected block below it. Renders just the title
    when no goal is configured (goal is None or <= 0)."""
    title_col, goal_col = st.columns([3, 1])
    with title_col:
        st.markdown(f"**{title}**")
    with goal_col:
        if goal and goal > 0:
            progress = min((current or 0) / goal, 1.0)
            st.markdown(
                f"<div style='text-align:right;font-size:12px;margin-top:4px'>"
                f"{current:,.0f} / {goal:,.0f} {unit_label} ({progress*100:.0f}%)</div>"
                f"<div style='background:rgba(150,150,150,0.25);border-radius:4px;height:6px;overflow:hidden'>"
                f"<div style='width:{progress*100:.1f}%;background:{color};height:100%;margin-left:auto'></div>"
                f"</div>",
                unsafe_allow_html=True,
            )


def _render_top_months_table(ranked, value_fmt, title="Top Ten Months by Distance", n=10):
    """Render a ranked table of the top ``n`` months by distance (descending).

    ``ranked`` is the DataFrame from process_data.rank_months_by_distance /
    rank_equity_months; ``value_fmt`` formats the native ``value`` for display.
    """
    st.subheader(title)
    if ranked is None or ranked.empty:
        st.info("No monthly data to rank yet.")
        return
    top = ranked.head(n).reset_index(drop=True)
    disp = pd.DataFrame({
        "Rank":       range(1, len(top) + 1),
        "Month":      top['label'],
        "Distance":   top['value'].apply(value_fmt),
        "Activities": top['count'].astype(int),
    })
    st.dataframe(disp, hide_index=True, width="stretch")


def _section_toc(items, color):
    """Render a 'Jump to' table of contents linking to header anchors lower on
    the page. ``items`` is a list of (label, anchor_id); ``color`` is the accent.
    Rendered large and underlined, with a ↓ cue, so it clearly reads as a set of
    in-page jump links rather than a caption."""
    links = " &nbsp;&nbsp;·&nbsp;&nbsp; ".join(
        f"<a href='#{anchor}' style='color:{color};font-weight:600;"
        f"text-decoration:underline;text-underline-offset:3px'>{label} ↓</a>"
        for label, anchor in items
    )
    st.markdown(
        f"<div style='margin:12px 0 6px;font-size:18px'>"
        f"<span style='font-weight:700;margin-right:8px'>Jump to:</span>{links}"
        f"</div>",
        unsafe_allow_html=True,
    )


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


# --- ISO-week navigation helpers (used by render_week_view's prev/next arrows) ---
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
    """Bike tab only: the annual *hours* chart. Despite the generic name this
    renders one specific chart, not a general year-view — annual *distance*
    is already shown by the thin overview chart above it in render_bike_tab,
    so this just adds the other dimension (dist_col/dist_label are accepted
    but currently unused)."""
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
def render_month_view(df, dist_label, *, key_prefix='bike', value_col='distance_miles',
                      unit_mult=1.0, value_label='Distance', count_label='Rides', color=None):
    """Day-by-day comparison of a calendar month vs. the same month last year
    (plus the current in-progress month, if different). Sport-agnostic: point
    value_col at whatever this sport's distance/effort column is."""
    import calendar
    today = date.today()
    current_year, current_month = today.year, today.month
    default_year, default_month = _last_complete_month()

    year_key, month_key = f'{key_prefix}_ref_year', f'{key_prefix}_ref_month'
    if year_key not in st.session_state:
        st.session_state[year_key] = default_year
    if month_key not in st.session_state:
        st.session_state[month_key] = default_month

    ref_year = st.session_state[year_key]
    ref_month = st.session_state[month_key]

    # Navigator row
    nav_l, nav_mid, nav_r = st.columns([1, 3, 1])
    with nav_l:
        if st.button("◀ Prev", key=f"{key_prefix}_month_prev"):
            ref_date = date(ref_year, ref_month, 1) - timedelta(days=1)
            st.session_state[year_key] = ref_date.year
            st.session_state[month_key] = ref_date.month
            st.rerun()
    with nav_mid:
        label = f"{calendar.month_name[ref_month]} {ref_year}"
        st.markdown(f"<h4 style='text-align:center;margin:0'>{label}</h4>", unsafe_allow_html=True)
    with nav_r:
        if st.button("Next ▶", key=f"{key_prefix}_month_next"):
            ref_date = date(ref_year, ref_month, 28) + timedelta(days=4)
            ref_date = ref_date.replace(day=1)
            st.session_state[year_key] = ref_date.year
            st.session_state[month_key] = ref_date.month
            st.rerun()

    # Aggregate data
    ref_df = process_data.aggregate_by_month(df, ref_year, ref_month, value_col=value_col)
    prior_df = process_data.aggregate_by_month(df, ref_year - 1, ref_month, value_col=value_col)
    ref_df['value'] *= unit_mult
    prior_df['value'] *= unit_mult

    # Shadow = current month only if different from ref
    is_current = (ref_year == current_year and ref_month == current_month)
    shadow_df = None
    if not is_current:
        shadow_df = process_data.aggregate_by_month(df, current_year, current_month, value_col=value_col)
        shadow_df['value'] *= unit_mult

    # Stats panels — shown before the chart
    ref_stats = process_data.get_period_stats(df, ref_year, month=ref_month, value_col=value_col)
    prior_stats = process_data.get_period_stats(df, ref_year - 1, month=ref_month, value_col=value_col)

    stat_cols = st.columns(3 if not is_current else 2)
    _render_stat_block(stat_cols[0], f"{calendar.month_name[ref_month]} {ref_year}", ref_stats, dist_label, unit_mult=unit_mult, value_label=value_label, count_label=count_label)
    _render_stat_block(stat_cols[1], f"{calendar.month_name[ref_month]} {ref_year - 1}", prior_stats, dist_label, unit_mult=unit_mult, value_label=value_label, count_label=count_label)
    if not is_current:
        shadow_stats = process_data.get_period_stats(df, current_year, month=current_month, value_col=value_col)
        _render_stat_block(stat_cols[2], f"{calendar.month_name[current_month]} {current_year} (YTD)", shadow_stats, dist_label, unit_mult=unit_mult, value_label=value_label, count_label=count_label)

    fig = make_period_comparison_chart(
        ref_df=ref_df,
        prior_df=prior_df,
        shadow_df=shadow_df,
        x_col='day',
        x_label='Day of Month',
        dist_col='value',
        dist_label=dist_label,
        title=f"{calendar.month_name[ref_month]} {ref_year} vs prior year"
              + ("" if is_current else f" + {calendar.month_name[current_month]} {current_year} (current)"),
        ref_color=color,
    )
    st.plotly_chart(fig)


# ---------------------------------------------------------------------------
# Render functions — Week view
# ---------------------------------------------------------------------------
def render_week_view(df, dist_label, *, key_prefix='bike', value_col='distance_miles',
                     unit_mult=1.0, value_label='Distance', count_label='Rides', color=None):
    """Weekday-by-weekday comparison of an ISO week vs. the same week last
    year (plus the current in-progress week, if different). Sport-agnostic —
    see render_month_view."""
    today = date.today()
    current_cal = today.isocalendar()
    current_iso_year, current_iso_week = current_cal[0], current_cal[1]
    default_iso_year, default_iso_week = _last_complete_iso_week()

    year_key, week_key = f'{key_prefix}_ref_iso_year', f'{key_prefix}_ref_iso_week'
    if year_key not in st.session_state:
        st.session_state[year_key] = default_iso_year
    if week_key not in st.session_state:
        st.session_state[week_key] = default_iso_week

    ref_iso_year = st.session_state[year_key]
    ref_iso_week = st.session_state[week_key]

    # Navigator row
    nav_l, nav_mid, nav_r = st.columns([1, 3, 1])
    with nav_l:
        if st.button("◀ Prev", key=f"{key_prefix}_week_prev"):
            y, w = _prev_iso_week(ref_iso_year, ref_iso_week)
            st.session_state[year_key] = y
            st.session_state[week_key] = w
            st.rerun()
    with nav_mid:
        st.markdown(
            f"<h4 style='text-align:center;margin:0'>{_week_label(ref_iso_year, ref_iso_week)}</h4>",
            unsafe_allow_html=True,
        )
    with nav_r:
        if st.button("Next ▶", key=f"{key_prefix}_week_next"):
            y, w = _next_iso_week(ref_iso_year, ref_iso_week)
            st.session_state[year_key] = y
            st.session_state[week_key] = w
            st.rerun()

    # Aggregate data
    ref_df = process_data.aggregate_by_iso_week(df, ref_iso_year, ref_iso_week, value_col=value_col)
    prior_df = process_data.aggregate_by_iso_week(df, ref_iso_year - 1, ref_iso_week, value_col=value_col)
    ref_df['value'] *= unit_mult
    prior_df['value'] *= unit_mult

    is_current = (ref_iso_year == current_iso_year and ref_iso_week == current_iso_week)
    shadow_df = None
    if not is_current:
        shadow_df = process_data.aggregate_by_iso_week(df, current_iso_year, current_iso_week, value_col=value_col)
        shadow_df['value'] *= unit_mult

    # Stats panels — shown before the chart
    ref_stats = process_data.get_period_stats(df, ref_iso_year, iso_week=ref_iso_week, value_col=value_col)
    prior_stats = process_data.get_period_stats(df, ref_iso_year - 1, iso_week=ref_iso_week, value_col=value_col)

    stat_cols = st.columns(3 if not is_current else 2)
    _render_stat_block(stat_cols[0], _week_label(ref_iso_year, ref_iso_week), ref_stats, dist_label, unit_mult=unit_mult, value_label=value_label, count_label=count_label)
    _render_stat_block(stat_cols[1], _week_label(ref_iso_year - 1, ref_iso_week) + " (prior)", prior_stats, dist_label, unit_mult=unit_mult, value_label=value_label, count_label=count_label)
    if not is_current:
        shadow_stats = process_data.get_period_stats(df, current_iso_year, iso_week=current_iso_week, value_col=value_col)
        _render_stat_block(stat_cols[2], "Current week (in progress)", shadow_stats, dist_label, unit_mult=unit_mult, value_label=value_label, count_label=count_label)

    fig = make_period_comparison_chart(
        ref_df=ref_df,
        prior_df=prior_df,
        shadow_df=shadow_df,
        x_col='day_label',
        x_label='Day',
        dist_col='value',
        dist_label=dist_label,
        title=_week_label(ref_iso_year, ref_iso_week)
              + ("" if is_current else f" + current week (in progress)"),
        ref_color=color,
    )
    st.plotly_chart(fig)


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------
def _render_stat_block(col, label, stats, dist_label, *, unit_mult=1.0, value_label='Distance', count_label='Rides'):
    dist_val = f"{stats['value'] * unit_mult:,.1f} {dist_label}"
    with col:
        st.markdown(f"**{label}**")
        st.metric(value_label, dist_val)
        st.metric("Hours", f"{stats['hours']:,.1f} h")
        st.metric(count_label, str(int(stats['count'])))


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


def render_bike_tab(bike_df, gear_map, settings):
    """Bike tab, top to bottom: all-time stats + route-heatmap thumbnail,
    annual distance chart, all-time monthly pattern, a year section (selector
    + monthly chart with goal line + 4-stat box), Most Recent/Longest/Top
    Months tables, a by-bike gear filter, the full-size route heatmap,
    all-time miles per bike, and an Experiments section (Year/Month/Week
    comparison views). Bike/Snow/Swim each follow this same shape; Running
    and Hiking use the generalized render_activity_tab version of it instead
    of duplicating this function."""
    current_year = date.today().year
    _unit = st.session_state.get('bike_unit', 'Miles')
    _is_mi = _unit == 'Miles'

    # Gear selection comes from session_state (the filter UI sits at the bottom).
    gear_ids = sorted(
        bike_df['gear_id'].unique().tolist(),
        key=lambda g: gear_map.get(g, g) if isinstance(g, str) else '',
    )
    selected_gears = [
        gid for gid in gear_ids
        if st.session_state.get(f"bike_gear_{gid}", True)
    ]
    filtered_df = bike_df[bike_df['gear_id'].isin(selected_gears)]
    yearly_all = process_data.aggregate_by_year(filtered_df)
    bike_months_ranked = process_data.rank_months_by_distance(filtered_df, 'distance_miles')

    _du    = 'mi' if _is_mi else 'km'
    _conv  = (lambda mi: mi) if _is_mi else (lambda mi: mi * 1.60934)

    _dc, _dl = ('miles', 'Miles') if _is_mi else ('km', 'Km')

    # --- All-time stats line (left) + static heatmap thumbnail (right) ---
    _stats_col, _thumb_col = st.columns([2.7, 1.3])
    with _stats_col:
        if not filtered_df.empty and not yearly_all.empty:
            _tot   = yearly_all['miles'].sum()
            _best  = yearly_all.loc[yearly_all['miles'].idxmax()]
            _long  = filtered_df.loc[filtered_df['distance_miles'].idxmax()]
            _lm    = bike_months_ranked.iloc[0]
            _ref   = settings.get('reference_sport', 'Bike')
            _brate = settings.get('conversions', {}).get('bike_miles_per_ref_unit', 1.0)
            _eq    = 0 if _ref == 'Bike' else (_tot / _brate if _brate else 0)
            _hrs   = yearly_all['hours'].sum()
            _cnt   = int(yearly_all['count'].sum())
            _all_time_line(
                distance=f"{_conv(_tot):,.0f} {_du}",
                hours=f"{_hrs:,.0f} h",
                activities=f"{_cnt:,}",
                seasons=str(filtered_df['year'].nunique()),
                best_year=f"{int(_best['year'])} · {_conv(_best['miles']):,.0f} {_du}",
                largest_month=f"{_conv(_lm['value']):,.0f} {_du} · {_lm['label']}",
                highest=f"{_conv(_long['distance_miles']):,.1f} {_du} · {_fmt_date(_long['start_date_local'])}",
                equity=f"{_eq:,.0f}",
                avg=f"{_conv(filtered_df['distance_miles'].mean()):,.1f} {_du}",
                avg_time=_fmt_time(_hrs * 3600 / _cnt) if _cnt else "—",
                avg_speed=f"{_conv(_tot) / _hrs:,.1f} {_du}/h" if _hrs else "—",
            )
    with _thumb_col:
        _bike_img_path = (settings.get('images', {}) or {}).get('bike_path') or config.BIKE_DEFAULT_IMAGE
        if os.path.exists(_bike_img_path):
            st.image(_bike_img_path, width="stretch")

    # --- Annual distance chart (full width, all bikes) ---
    _yearly_unfiltered = process_data.aggregate_by_year(bike_df)
    if not _yearly_unfiltered.empty:
        st.plotly_chart(
            make_year_dist_chart(_yearly_unfiltered, _dc, _dl, current_year, height=220),
        )

    # --- All-time monthly pattern (which calendar months you actually ride) ---
    _alltime_monthly_bike = process_data.aggregate_bike_by_month(filtered_df)
    if _alltime_monthly_bike['count'].sum() > 0:
        st.plotly_chart(
            make_monthly_chart(
                _alltime_monthly_bike, _dc, _dl,
                title=f"All-Time {_dl} by Month",
            ),
        )

    # --- Units control ---
    unit = st.radio(
        "Units", ["Miles", "Km"],
        horizontal=True, key="bike_unit",
    )

    dist_col = 'miles' if unit == 'Miles' else 'km'
    dist_label = 'Miles' if unit == 'Miles' else 'Km'

    # --- Year section: selector + monthly chart + stats box (mirrors the
    # Snow season section and Swim year section) ---
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

        monthly_bike = process_data.aggregate_bike_by_month(filtered_df, selected_year)
        _bike_goal_series = process_data.bike_monthly_goal_series(settings)
        if dist_col == 'km':
            _bike_goal_series = [v * 1.60934 for v in _bike_goal_series]
        st.plotly_chart(
            make_monthly_chart(monthly_bike, dist_col, dist_label, goal=_bike_goal_series),
        )

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

    if unit == 'Miles':
        fmt_bike = lambda r: f"{r['distance_miles']:,.1f} mi"
    else:
        fmt_bike = lambda r: f"{r['distance'] / 1000:.1f} km"

    # --- Table of contents for the list sections below ---
    _section_toc(
        [("Most Recent Rides", "most-recent-rides"),
         ("Longest Rides",     "longest-rides"),
         ("Top Ten Months",    "top-ten-months-by-distance")],
        STRAVA_ORANGE,
    )

    st.divider()
    _render_recent_table(filtered_df, fmt_bike, "Most Recent Rides", key_prefix="bike")

    st.divider()
    _render_longest_table(filtered_df, 'distance_miles', fmt_bike, "Longest Rides")

    st.divider()
    _bike_month_fmt = (lambda v: f"{v:,.0f} mi") if unit == 'Miles' else (lambda v: f"{v * 1.60934:,.0f} km")
    _render_top_months_table(bike_months_ranked, _bike_month_fmt)

    st.divider()

    # --- Gear filter (bottom) ---
    st.markdown("**Filter by bike:**")
    gear_cols = st.columns(min(len(gear_ids), 4))
    for i, gid in enumerate(gear_ids):
        label = gear_map.get(gid, gid) if gid else "Unknown Bike"
        gear_cols[i % len(gear_cols)].checkbox(label, value=True, key=f"bike_gear_{gid}")

    st.divider()
    render_bike_heatmap_view(compact=False)

    # --- Top bikes by all-time miles (unaffected by the gear filter above) ---
    st.divider()
    if not bike_df.empty:
        _bike_totals = (
            bike_df.groupby('gear_id')['distance_miles'].sum()
            .sort_values(ascending=False)
            .head(4)
        )
        if not _bike_totals.empty:
            st.markdown("**Top Bikes — All-Time Miles**")
            _stats_box([
                (gear_map.get(gid, gid) if gid else "Unknown Bike", f"{_conv(mi):,.0f} {_du}")
                for gid, mi in _bike_totals.items()
            ])

    # --- Experiments: Year/Month/Week comparison tooling ---
    st.divider()
    st.subheader("Experiments")
    time_mode = st.radio(
        "Time mode", ["Year", "Month", "Week"],
        horizontal=True, key="bike_time_mode",
    )
    if time_mode == "Year":
        render_year_view(filtered_df, dist_col, dist_label)
    elif time_mode == "Month":
        render_month_view(
            filtered_df, dist_label, key_prefix='bike', value_col='distance_miles',
            unit_mult=(1.0 if dist_col == 'miles' else 1.60934),
            value_label='Distance', count_label='Rides', color=STRAVA_ORANGE,
        )
    elif time_mode == "Week":
        render_week_view(
            filtered_df, dist_label, key_prefix='bike', value_col='distance_miles',
            unit_mult=(1.0 if dist_col == 'miles' else 1.60934),
            value_label='Distance', count_label='Rides', color=STRAVA_ORANGE,
        )


# ---------------------------------------------------------------------------
# Ski tab
# ---------------------------------------------------------------------------
def render_ski_tab(ski_df, settings):
    """Snow tab. Mirrors render_bike_tab's overall shape, but "distance" is
    vertical feet and the period unit is a ski *season* (e.g. "2025-2026",
    keyed by its start year — Oct-Dec belongs to that year, Jan-Sep to the
    following one, see process_data._ski_season_key) rather than a calendar
    year. Numbered comments below walk the section order."""
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
    ski_months_ranked = process_data.rank_months_by_distance(ski_df, 'elevation_feet')

    # --- All-time stats line (left) + snow image (right). Snow measures
    # "distance" in vertical feet. ---
    _stats_col, _img_col = st.columns([2.7, 1.3])
    with _stats_col:
        if not seasonal_df.empty:
            _all_vert     = seasonal_df['vert_ft'].sum()
            _all_sessions = int(seasonal_df['sessions'].sum())
            _best_season  = seasonal_df.loc[seasonal_df['vert_ft'].idxmax()]
            _big          = ski_df.loc[ski_df['elevation_feet'].idxmax()]
            _lm           = ski_months_ranked.iloc[0]
            _all_eq       = _all_vert / ski_vert_per_mile if ski_vert_per_mile > 0 else 0
            _avg_vert     = _all_vert / _all_sessions if _all_sessions else 0
            _all_secs     = ski_df['moving_time'].sum()
            _all_hrs      = _all_secs / 3600
            _all_time_line(
                distance=f"{_all_vert:,.0f} ft",
                hours=f"{_all_hrs:,.0f} h",
                activities=f"{_all_sessions:,}",
                seasons=str(len(seasonal_df)),
                best_year=f"{_best_season['season_label']} · {_best_season['vert_ft']:,.0f} ft",
                largest_month=f"{_lm['value']:,.0f} ft · {_lm['label']}",
                highest=f"{_big['elevation_feet']:,.0f} ft · {_fmt_date(_big['start_date_local'])}",
                equity=f"{_all_eq:,.0f}",
                avg=f"{_avg_vert:,.0f} ft",
                avg_time=_fmt_time(_all_secs / _all_sessions) if _all_sessions else "—",
                avg_speed=f"{_all_vert / _all_hrs:,.0f} ft/h" if _all_hrs else "—",
            )
    with _img_col:
        _img_path = (settings.get('images', {}) or {}).get('snow_path') or config.SNOW_DEFAULT_IMAGE
        if os.path.exists(_img_path):
            st.image(_img_path, width="stretch")

    # --- 1. All-seasons overview chart (full width); header carries the
    # season-goal readout since the chart already draws the goal as a
    # dashed line — no need to repeat it as a block further down. ---
    _current_row = seasonal_df[seasonal_df['season_key'] == current_season_key]
    _current_vert = _current_row.iloc[0]['vert_ft'] if not _current_row.empty else 0
    _chart_header_with_goal(
        "Season Vertical Feet", current=_current_vert, goal=goal_vert, unit_label="ft", color=SKI_BLUE,
    )
    st.plotly_chart(
        make_season_vert_chart(seasonal_df, current_season_key, goal_vert=goal_vert, height=220, title=" "),
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
    )
    selected_key = label_to_key[selected_label]

    # --- 3. Vert by Month chart (season months, spanning both calendar years) ---
    monthly_season = _agg_ski_season_by_month(ski_df, selected_key, ski_start, ski_end)
    if not monthly_season.empty:
        st.plotly_chart(
            make_monthly_chart(monthly_season, 'vert_ft', 'ft', color=SKI_BLUE),
        )

    # --- 4. Stats box ---
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

    # --- Top 5 Ski Days (vertical feet) for the selected season ---
    _season_key_per_row = ski_df['start_date_local'].apply(process_data._ski_season_key)
    season_df = ski_df[_season_key_per_row == selected_key]
    _render_longest_table(
        season_df, 'elevation_feet',
        lambda r: f"{r['elevation_feet']:,.0f} ft",
        f"Top 5 Ski Days — {selected_label}", n=5,
    )

    # --- Second, thin Vert by Month chart for the selected season ---
    if not monthly_season.empty:
        st.plotly_chart(
            make_monthly_chart(monthly_season, 'vert_ft', 'ft', color=SKI_BLUE_LIGHT, title=" ", height=180),
        )

    # --- 4b. Table of contents for the list sections below ---
    _section_toc(
        [("Most Recent Snow Activities", "most-recent-snow-activities"),
         ("Biggest Snow Days",           "biggest-snow-days-all-seasons"),
         ("Top Ten Months",             "top-ten-months-by-distance"),
         ("Snow Days",                   "snow-days")],
        SKI_BLUE,
    )

    # --- 5. Most Recent Snow Activities ---
    st.divider()
    _render_recent_table(
        ski_df,
        lambda r: f"{r['elevation_feet']:,.0f} ft vert",
        "Most Recent Snow Activities",
        key_prefix="ski",
        widget="number",
    )

    # --- 6. Biggest Snow Days (All Seasons) ---
    st.divider()
    _render_longest_table(
        ski_df, 'elevation_feet',
        lambda r: f"{r['elevation_feet']:,.0f} ft",
        "Biggest Snow Days (All Seasons)",
    )

    # --- 6b. Top Ten Months by vertical feet ---
    st.divider()
    _render_top_months_table(ski_months_ranked, lambda v: f"{v:,.0f} ft")

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

    # --- 8. Experiments: Month/Week comparison tooling ---
    st.divider()
    st.subheader("Experiments")
    ski_time_mode = st.radio(
        "Compare", ["Month", "Week"], horizontal=True, key="ski_time_mode",
    )
    if ski_time_mode == "Month":
        render_month_view(
            ski_df, 'ft', key_prefix='ski', value_col='elevation_feet',
            value_label='Vertical', count_label='Sessions', color=SKI_BLUE,
        )
    else:
        render_week_view(
            ski_df, 'ft', key_prefix='ski', value_col='elevation_feet',
            value_label='Vertical', count_label='Sessions', color=SKI_BLUE,
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
    """Swim tab. Mirrors render_bike_tab's overall shape, keyed on distance
    (meters/yards, toggled by the Units radio) with an "All time" option
    alongside individual years where Bike/Snow only offer single periods.
    The ``df`` parameter is accepted but currently unused."""
    if swim_df.empty:
        st.info("No swim activities found in the archive.")
        return

    monthly_goal_m = settings['goals']['swim_monthly_meters']
    seasons = settings.get('seasons', {})
    swim_start = seasons.get('swim_start_month', 5)
    swim_end   = seasons.get('swim_end_month', 9)
    current_year = date.today().year

    # --- 1. All-time stats line (left) + pool image (right) ---
    yearly = _agg_swim_by_year(swim_df)
    swim_months_ranked = process_data.rank_months_by_distance(swim_df, 'distance')
    # Units are chosen by the radio further down; read the current choice from
    # session_state so this top line already reflects it on rerun.
    _unit   = st.session_state.get('swim_unit', 'Meters')
    _mult   = 1.0 if _unit == 'Meters' else 1.09361
    _dlabel = 'm' if _unit == 'Meters' else 'yd'
    _stats_col, _img_col = st.columns([2.7, 1.3])
    with _stats_col:
        if not yearly.empty:
            _all_m    = yearly['meters'].sum()
            _all_sw   = int(yearly['swims'].sum())
            _best     = yearly.loc[yearly['meters'].idxmax()]
            _long     = swim_df.loc[swim_df['distance'].idxmax()]
            _lm       = swim_months_ranked.iloc[0]
            _swim_per = settings.get('conversions', {}).get(
                'swim_meters_per_ref_unit',
                settings.get('conversions', {}).get('swim_meters_per_mile', 100))
            _eq       = _all_m / _swim_per if _swim_per else 0
            _avg      = (_all_m / _all_sw) if _all_sw else 0
            _all_secs = swim_df['moving_time'].sum()
            _all_hrs  = _all_secs / 3600
            _all_time_line(
                distance=f"{_all_m * _mult:,.0f} {_dlabel}",
                hours=f"{_all_hrs:,.0f} h",
                activities=f"{_all_sw:,}",
                seasons=str(swim_df['year'].nunique()),
                best_year=f"{int(_best['year'])} · {_best['meters'] * _mult:,.0f} {_dlabel}",
                largest_month=f"{_lm['value'] * _mult:,.0f} {_dlabel} · {_lm['label']}",
                highest=f"{_long['distance'] * _mult:,.0f} {_dlabel} · {_fmt_date(_long['start_date_local'])}",
                equity=f"{_eq:,.0f}",
                avg=f"{_avg * _mult:,.0f} {_dlabel}",
                avg_time=_fmt_time(_all_secs / _all_sw) if _all_sw else "—",
                avg_speed=f"{_all_m * _mult / _all_hrs:,.0f} {_dlabel}/h" if _all_hrs else "—",
            )
    with _img_col:
        _img_path = (settings.get('images', {}) or {}).get('swim_path') or config.SWIM_DEFAULT_IMAGE
        if os.path.exists(_img_path):
            st.image(_img_path, width="stretch")

    # --- 2. Multi-year overview chart (full width) ---
    if not yearly.empty:
        _dc = 'meters' if _unit == 'Meters' else 'yards'
        yearly_plot = yearly.rename(columns={_dc: '_dist'})[['year', 'swims', '_dist']].copy()
        yearly_plot.columns = ['year', 'swims', _dc]
        st.plotly_chart(
            make_swim_year_chart(yearly_plot, current_year, height=220),
        )

    # --- All-time monthly pattern (which calendar months you actually swim) ---
    _alltime_monthly_swim = _agg_swim_by_month(swim_df, None)
    _dc_all = 'meters' if _unit == 'Meters' else 'yards'
    if _alltime_monthly_swim['swims'].sum() > 0:
        st.plotly_chart(
            make_monthly_chart(
                _alltime_monthly_swim, _dc_all, _dlabel,
                title=f"All-Time {_dlabel} by Month", color=SWIM_TEAL,
            ),
        )

    # --- 3. Controls: Year + Units ---
    ctrl_l, ctrl_r = st.columns(2)
    with ctrl_l:
        years = sorted(swim_df['year'].unique().tolist(), reverse=True)
        year_options = ["All time"] + [str(y) for y in years]
        selected_year_label = st.selectbox("Year", year_options, key="swim_year")
    with ctrl_r:
        unit = st.radio("Units", ["Meters", "Yards"], horizontal=True, key="swim_unit")

    is_all_time = selected_year_label == "All time"
    selected_year = None if is_all_time else int(selected_year_label)
    period_df  = swim_df if is_all_time else swim_df[swim_df['year'] == selected_year]

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

    # --- Period stats (used by both the goal header and the stats box) ---
    total_dist  = period_df['distance'].sum() * mult
    total_swims = len(period_df)
    avg_dist    = (total_dist / total_swims) if total_swims else 0
    months_with_data = int((monthly[dist_col] > 0).sum())
    avg_monthly = total_dist / months_with_data if months_with_data else 0
    max_swim    = period_df['distance'].max() * mult if not period_df.empty else 0

    # --- Distance by Month chart, header carries the goal-pace readout ---
    _period_label = "All Time" if is_all_time else str(selected_year)
    _chart_header_with_goal(
        f"{_period_label} Distance by Month ({dist_label})",
        current=avg_monthly, goal=goal_val, unit_label=f"{dist_label} avg", color=SWIM_TEAL,
    )
    st.plotly_chart(
        make_monthly_chart(monthly, dist_col, dist_label, goal=goal_val, color=SWIM_TEAL, title=" "),
    )

    _stats_box([
        ("Total Distance", f"{total_dist:,.0f} {dist_label}"),
        ("Swims",          str(total_swims)),
        ("Longest Swim",   f"{max_swim:,.0f} {dist_label}"),
        ("Avg per Swim",   f"{avg_dist:,.0f} {dist_label}"),
        ("Avg per Month",  f"{avg_monthly:,.0f} {dist_label}"),
    ])

    fmt_swim = (lambda r: f"{r['distance']:,.0f} m") if unit == 'Meters' else (lambda r: f"{r['distance'] * 1.09361:,.0f} yd")

    # --- Top 5 Swims for the selected period ---
    _render_longest_table(period_df, 'distance', fmt_swim, f"Top 5 Swims — {_period_label}", n=5)

    # --- Second, thin monthly chart for the selected period ---
    st.plotly_chart(
        make_monthly_chart(monthly, dist_col, dist_label, color=SWIM_TEAL_LIGHT, title=" ", height=180),
    )

    # --- 6. Table of contents for the list sections below ---
    _section_toc(
        [("Most Recent Swims",       "most-recent-swims"),
         ("All-time Longest Swims",  "all-time-longest-swims"),
         ("Top Ten Months",          "top-ten-months-by-distance")],
        SWIM_TEAL,
    )

    # --- 7. Most Recent Swims ---
    st.divider()
    _render_recent_table(swim_df, fmt_swim, "Most Recent Swims", key_prefix="swim", widget="number")

    # --- 8. All-time Longest Swims ---
    st.divider()
    _render_longest_table(swim_df, 'distance', fmt_swim, "All-time Longest Swims")

    # --- 9. Top Ten Months by distance ---
    st.divider()
    _swim_month_fmt = (lambda v: f"{v:,.0f} m") if unit == 'Meters' else (lambda v: f"{v * 1.09361:,.0f} yd")
    _render_top_months_table(swim_months_ranked, _swim_month_fmt)

    # --- 10. Experiments: Month/Week comparison tooling ---
    st.divider()
    st.subheader("Experiments")
    swim_time_mode = st.radio(
        "Compare", ["Month", "Week"], horizontal=True, key="swim_time_mode",
    )
    if swim_time_mode == "Month":
        render_month_view(
            swim_df, dist_label, key_prefix='swim', value_col='distance',
            unit_mult=mult, value_label='Distance', count_label='Swims', color=SWIM_TEAL,
        )
    else:
        render_week_view(
            swim_df, dist_label, key_prefix='swim', value_col='distance',
            unit_mult=mult, value_label='Distance', count_label='Swims', color=SWIM_TEAL,
        )


# ---------------------------------------------------------------------------
# Shared tab — Running / Hiking (sports without a dedicated photo, gear
# heatmap, or goal-progress bells; same section order as Bike/Snow/Swim
# otherwise: all-time stats, annual chart, all-time monthly chart, a year
# section, ranked tables, and an Experiments section).
# ---------------------------------------------------------------------------
def render_activity_tab(df, gear_map, settings, *, sport_key, label, color, color_light=None,
                        count_noun='Activities', gear_noun='Gear', ref_label=None):
    if df.empty:
        st.info(f"No {label.lower()} activities found in the archive.")
        return

    current_year = date.today().year

    # Gear selection comes from session_state (the filter UI sits near the
    # bottom) — only meaningful if this sport actually has gear tagged.
    gear_ids = sorted(
        df['gear_id'].dropna().unique().tolist(),
        key=lambda g: gear_map.get(g, g) if isinstance(g, str) else '',
    )
    has_gear = len(gear_ids) > 0
    if has_gear:
        selected_gears = [
            gid for gid in gear_ids
            if st.session_state.get(f"{sport_key}_gear_{gid}", True)
        ]
        filtered_df = df[df['gear_id'].isin(selected_gears)]
    else:
        filtered_df = df

    yearly_all = process_data.aggregate_by_year(filtered_df)
    months_ranked = process_data.rank_months_by_distance(filtered_df, 'distance_miles')

    # --- All-time stats line (full width — no photo for this sport) ---
    if not filtered_df.empty and not yearly_all.empty:
        _tot   = yearly_all['miles'].sum()
        _best  = yearly_all.loc[yearly_all['miles'].idxmax()]
        _long  = filtered_df.loc[filtered_df['distance_miles'].idxmax()]
        _lm    = months_ranked.iloc[0]
        _ref   = settings.get('reference_sport', 'Bike')
        _rate_key = {'run': 'run_miles_per_ref_unit', 'hike': 'hike_miles_per_ref_unit'}.get(sport_key)
        _rate  = settings.get('conversions', {}).get(_rate_key, 1.0) if _rate_key else 1.0
        _eq    = 0 if _ref == ref_label else (_tot / _rate if _rate else 0)
        _hrs   = yearly_all['hours'].sum()
        _cnt   = int(yearly_all['count'].sum())
        _all_time_line(
            distance=f"{_tot:,.0f} mi",
            hours=f"{_hrs:,.0f} h",
            activities=f"{_cnt:,}",
            seasons=str(filtered_df['year'].nunique()),
            best_year=f"{int(_best['year'])} · {_best['miles']:,.0f} mi",
            largest_month=f"{_lm['value']:,.0f} mi · {_lm['label']}",
            highest=f"{_long['distance_miles']:,.1f} mi · {_fmt_date(_long['start_date_local'])}",
            equity=f"{_eq:,.0f}",
            avg=f"{filtered_df['distance_miles'].mean():,.1f} mi",
            avg_time=_fmt_time(_hrs * 3600 / _cnt) if _cnt else "—",
            avg_speed=f"{_tot / _hrs:,.1f} mi/h" if _hrs else "—",
        )

    # --- Top gear by all-time miles (unaffected by the gear filter below) ---
    if has_gear:
        _gear_totals = (
            df.groupby('gear_id')['distance_miles'].sum()
            .sort_values(ascending=False)
            .head(4)
        )
        if not _gear_totals.empty:
            st.markdown(f"**Top {gear_noun} — All-Time Miles**")
            _stats_box([
                (gear_map.get(gid, gid), f"{mi:,.0f} mi")
                for gid, mi in _gear_totals.items()
            ])

    # --- Annual distance chart (full width) ---
    _yearly_unfiltered = process_data.aggregate_by_year(df)
    if not _yearly_unfiltered.empty:
        st.plotly_chart(
            make_year_dist_chart(_yearly_unfiltered, 'miles', 'Miles', current_year, height=220,
                                 color=color, color_light=color_light),
        )

    # --- All-time monthly pattern ---
    _alltime_monthly = process_data.aggregate_bike_by_month(filtered_df)
    if _alltime_monthly['count'].sum() > 0:
        st.plotly_chart(
            make_monthly_chart(
                _alltime_monthly, 'miles', 'Miles',
                title="All-Time Miles by Month", color=color,
            ),
        )

    # --- Year section: selector + monthly chart + stats box ---
    available_years = sorted(filtered_df['year'].unique().tolist(), reverse=True) if not filtered_df.empty else []
    year_key = f'{sport_key}_year'
    _cur_yr = st.session_state.get(year_key)
    if _cur_yr not in available_years:
        st.session_state[year_key] = available_years[0] if available_years else None
    selected_year = st.selectbox("Year", available_years, key=year_key)

    if selected_year is not None and not yearly_all.empty:
        yr_row = yearly_all[yearly_all['year'] == selected_year]
        max_dist = filtered_df[filtered_df['year'] == selected_year]['distance_miles'].max() \
                   if not filtered_df[filtered_df['year'] == selected_year].empty else 0

        monthly = process_data.aggregate_bike_by_month(filtered_df, selected_year)
        st.plotly_chart(
            make_monthly_chart(monthly, 'miles', 'Miles', color=color),
        )

        if not yr_row.empty:
            r = yr_row.iloc[0]
            _stats_box([
                (f"{selected_year} Distance", f"{r['miles']:,.0f} mi"),
                ("Longest",                   f"{max_dist:,.1f} mi"),
                ("Hours",                     f"{r['hours']:,.0f} h"),
                (count_noun,                  f"{int(r['count']):,}"),
            ])

    fmt_activity = lambda r: f"{r['distance_miles']:,.1f} mi"

    # --- Table of contents for the list sections below ---
    _section_toc(
        [(f"Most Recent {count_noun}", f"most-recent-{count_noun.lower()}"),
         (f"Longest {count_noun}",     f"longest-{count_noun.lower()}"),
         ("Top Ten Months",            "top-ten-months-by-distance")],
        color,
    )

    st.divider()
    _render_recent_table(filtered_df, fmt_activity, f"Most Recent {count_noun}", key_prefix=sport_key)

    st.divider()
    _render_longest_table(filtered_df, 'distance_miles', fmt_activity, f"Longest {count_noun}")

    st.divider()
    _render_top_months_table(months_ranked, lambda v: f"{v:,.0f} mi")

    # --- Gear filter (bottom), only if this sport has gear tagged ---
    if has_gear:
        st.divider()
        st.markdown(f"**Filter by {gear_noun.lower()}:**")
        gear_cols = st.columns(min(len(gear_ids), 4))
        for i, gid in enumerate(gear_ids):
            gear_cols[i % len(gear_cols)].checkbox(
                gear_map.get(gid, gid), value=True, key=f"{sport_key}_gear_{gid}",
            )

    # --- Experiments: Month/Week comparison tooling ---
    st.divider()
    st.subheader("Experiments")
    activity_time_mode = st.radio(
        "Compare", ["Month", "Week"], horizontal=True, key=f"{sport_key}_time_mode",
    )
    if activity_time_mode == "Month":
        render_month_view(
            filtered_df, 'Miles', key_prefix=sport_key, value_col='distance_miles',
            value_label='Distance', count_label=count_noun, color=color,
        )
    else:
        render_week_view(
            filtered_df, 'Miles', key_prefix=sport_key, value_col='distance_miles',
            value_label='Distance', count_label=count_noun, color=color,
        )


# ---------------------------------------------------------------------------
# Combined tab (cross-sport equity)
# ---------------------------------------------------------------------------
def render_equity_tab(df, settings):
    """Combined tab: the app's cross-sport "equity miles" view. All-time stats
    line (total + each sport's share), a multi-year overview chart, a
    year-selector + stats box + monthly breakdown, a Top Ten Months table,
    and a table of manually-declared "custom" equity (see
    process_data.reconcile_equity_declarations for the counting policy)."""
    goals       = settings.get('goals', {})
    annual_goal = goals.get('annual_equity_miles', 0)
    monthly_goal= goals.get('monthly_equity_miles', 0)
    ref_label   = settings.get('reference_sport', 'Bike')

    today = date.today()
    current_year = today.year
    yearly = process_data.aggregate_equity_by_year(df, settings)
    eq_months_ranked = process_data.rank_equity_months(df, settings)

    # --- All-time stats line (top): total + each sport's contribution ---
    if not yearly.empty:
        all_total = yearly['total'].sum()
        all_items = [("All-Time Equity Miles", f"{all_total:,.0f}")]
        for col, label in [('bike','Bike'), ('run','Run'), ('ski','Ski'), ('swim','Swim'),
                           ('hike','Hike'), ('paddle','Paddle'), ('custom','Custom')]:
            if col in yearly.columns:
                all_val = yearly[col].sum()
                if all_val > 0.5:
                    all_pct = f" ({all_val / all_total * 100:.0f}%)" if all_total else ""
                    all_items.append((label, f"{all_val:,.0f} mi{all_pct}"))
        if not eq_months_ranked.empty:
            _lm = eq_months_ranked.iloc[0]
            all_items.append(("Largest Month", f"{_lm['value']:,.0f} mi · {_lm['label']}"))
        _stats_box(all_items)

    # --- 1. Thin, wide multi-year overview chart ---
    st.plotly_chart(
        make_equity_annual_chart(yearly, current_year, ref_label=ref_label, height=220),
    )

    # --- 2. Year selector ---
    available_years = sorted(df['year'].unique().tolist(), reverse=True)
    default_year = today.year - 1
    default_idx = available_years.index(default_year) if default_year in available_years else 0
    selected_year = st.selectbox("Year", available_years, index=default_idx, key="equity_year")

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

    # --- 5. Top Ten Months by equity miles ---
    st.divider()
    _render_top_months_table(eq_months_ranked, lambda v: f"{v:,.0f} mi")

    st.divider()

    # --- Custom Equity activities table ---
    st.subheader("Custom Equity Activities")
    st.caption(
        "Manually-declared equity (e.g. GEq gardening, ShovelEq, stationary bike) that the "
        "app counts because there's no matching real activity to convert. These feed the "
        "'Custom' slice above. Declarations that merely restate a tracked activity are dropped."
    )

    eq_df = process_data.get_eq_activities(df, settings)
    custom_eq = eq_df[eq_df['counts']] if not eq_df.empty else eq_df
    if custom_eq.empty:
        st.info("No counted equity declarations found.")
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
    display['Type']     = display['eq_prefix']
    display['Miles']    = display['miles'].apply(lambda v: f"{v:,.1f}")
    display = _add_strava_url(display).rename(columns={'strava_url': 'View on Strava'})
    st.dataframe(
        display[['Date', 'Activity', 'Type', 'Miles', 'View on Strava']],
        hide_index=True,
        column_config=_STRAVA_URL_COL_CONFIG,
    )


# ---------------------------------------------------------------------------
# Period / sport filter helpers — used by the Wrapped and Export tabs' period
# and activity-group selectors (not the Bike/Snow/Swim tabs, which use their
# own Year/Season/Month selectors instead).
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


def _prior_period_filter(df, meta):
    """Return the df slice for the period immediately before ``meta`` (for
    period-over-period deltas), or None when there's no sensible prior
    ('All time'). Year → prior year, month → same month last year, rolling N
    days → the N days before that."""
    today = date.today()
    ptype = meta['type']
    sd = df['start_date_local'].dt.date
    if ptype == 'rolling':
        n = meta['days']
        start = today - timedelta(days=2 * n)
        end   = today - timedelta(days=n)
        return df[(sd >= start) & (sd < end)].copy()
    elif ptype == 'year':
        return df[df['year'] == meta['year'] - 1].copy()
    elif ptype == 'month':
        return df[
            (df['year'] == meta['year'] - 1) &
            (df['start_date_local'].dt.month == meta['month'])
        ].copy()
    return None  # 'all'


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

# Edit this list to add, remove, or reorder columns in the longest activities
# table. "View on Strava" stays last so it renders as the rightmost column.
_LONGEST_COLS = [
    ('Date',     'date_str'),
    ('Activity', 'name'),
    ('Type',     'final_type'),
    ('Distance', 'dist_display'),
    ('Duration', 'duration_str'),
    ('View on Strava', 'strava_url'),
]

_STRAVA_URL_COL_CONFIG = {
    "View on Strava": st.column_config.LinkColumn(display_text="View on Strava"),
}


def _add_strava_url(df):
    """Add a strava_url column (https://www.strava.com/activities/<id>) for
    an activity-link table column. Per Strava's brand guidelines, activity
    data shown in a third-party app should link back to the original."""
    df = df.copy()
    df['strava_url'] = 'https://www.strava.com/activities/' + df['id'].astype(str)
    return df


def _render_recent_table(df, fmt_dist, title="Most Recent Activities",
                         key_prefix="recent", widget="slider"):
    """Render the N most recent activities with a control for N (default 5, max 20).
    widget='slider' (default) uses st.slider; widget='number' uses
    st.number_input — useful when a tab's color scheme conflicts with the
    slider's Streamlit-primary-color filled track."""
    st.subheader(title)
    if df.empty:
        st.info("No activities to display.")
        return

    if widget == "number":
        n = st.number_input(
            "Number to show", min_value=1, max_value=20, value=5, step=1,
            key=f"{key_prefix}_recent_n",
        )
    else:
        n = st.slider(
            "Number to show", min_value=1, max_value=20, value=5,
            key=f"{key_prefix}_recent_n",
        )
    recent = df.sort_values('start_date_local', ascending=False).head(n).copy()
    recent['date_str']     = pd.to_datetime(recent['start_date_local']).apply(_fmt_date)
    recent['dist_display'] = recent.apply(fmt_dist, axis=1)
    recent['duration_str'] = recent['moving_time'].apply(_fmt_time)
    recent = _add_strava_url(recent)
    src_cols = [src for _, src in _LONGEST_COLS]
    hdr_map  = {src: hdr for hdr, src in _LONGEST_COLS}
    st.dataframe(recent[src_cols].rename(columns=hdr_map), width="stretch", hide_index=True,
                column_config=_STRAVA_URL_COL_CONFIG)


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
    # Rank by distance descending; break ties by most-recent-first so equal
    # distances read in descending chronological order.
    top = df.sort_values(
        [sort_col, 'start_date_local'], ascending=[False, False],
    ).head(n).copy()
    top['date_str']     = pd.to_datetime(top['start_date_local']).apply(_fmt_date)
    top['dist_display'] = top.apply(fmt_dist, axis=1)
    top['duration_str'] = top['moving_time'].apply(_fmt_time)
    top = _add_strava_url(top)
    src_cols = [src for _, src in _LONGEST_COLS]
    hdr_map  = {src: hdr for hdr, src in _LONGEST_COLS}
    st.dataframe(top[src_cols].rename(columns=hdr_map), width="stretch", hide_index=True,
                column_config=_STRAVA_URL_COL_CONFIG)


# ---------------------------------------------------------------------------
# Wrapped tab — its own card-based visual identity, distinct from the plain
# _stats_box strip the other tabs share, so it reads as a "reveal" rather
# than another stats table.
# ---------------------------------------------------------------------------
def _wrapped_hero(value, label, caption=None):
    """The one headline number for the view — a bold gradient card, ≥48px
    figure. Exactly one per Wrapped render."""
    html = f'''
    <div style="
        background: linear-gradient(135deg, {STRAVA_ORANGE} 0%, #a12a06 100%);
        border-radius: 16px;
        padding: 28px 32px;
        margin: 10px 0 18px 0;
        text-align: center;
    ">
      <div style="font-size:13px;letter-spacing:0.08em;text-transform:uppercase;
                  color:rgba(255,255,255,0.85);font-weight:600">{label}</div>
      <div style="font-size:56px;font-weight:800;color:#ffffff;line-height:1.15;
                  margin-top:6px">{value}</div>
      {f'<div style="font-size:13px;color:rgba(255,255,255,0.85);margin-top:6px">{caption}</div>' if caption else ''}
    </div>
    '''
    st.markdown(html, unsafe_allow_html=True)


def _wrapped_cards(items, cols=4):
    """A row of bold accent-bordered cards — the Wrapped KPI/records/fun-facts
    unit. items: list of dicts with 'label', 'value', and optionally 'delta'
    (pre-signed string, e.g. '+340'), 'caption' (muted subtitle), or 'badge'
    (emoji prefix on the label, e.g. a 🏆 PR marker)."""
    dark = st.context.theme.type == 'dark'
    bg          = '#21232b' if dark else '#fffaf7'
    label_color = '#9ca3af' if dark else '#8a6a5c'
    value_color = '#f0f0f0' if dark else '#1f1f1f'
    caption_color = '#7d8590' if dark else '#9a8478'
    up_color    = '#3fb950' if dark else '#16a34a'
    down_color  = '#f85149' if dark else '#dc2626'

    columns = st.columns(cols)
    for i, item in enumerate(items):
        delta_html = ''
        if item.get('delta'):
            is_up = item['delta'].strip().startswith('+')
            dcolor = up_color if is_up else down_color
            arrow = '▲' if is_up else '▼'
            delta_html = (
                f'<div style="font-size:12px;color:{dcolor};margin-top:4px">'
                f'{arrow} {item["delta"].lstrip("+-")}</div>'
            )
        caption_html = (
            f'<div style="font-size:12px;color:{caption_color};margin-top:4px">{item["caption"]}</div>'
            if item.get('caption') else ''
        )
        badge = f'{item["badge"]} ' if item.get('badge') else ''
        html = f'''
        <div style="
            background:{bg};
            border-left: 4px solid {STRAVA_ORANGE};
            border-radius: 10px;
            padding: 14px 16px;
            margin-bottom: 12px;
        ">
          <div style="font-size:12px;color:{label_color};font-weight:600;
                      text-transform:uppercase;letter-spacing:0.03em">{badge}{item['label']}</div>
          <div style="font-size:26px;font-weight:700;color:{value_color};margin-top:3px">{item['value']}</div>
          {delta_html}{caption_html}
        </div>
        '''
        columns[i % cols].markdown(html, unsafe_allow_html=True)


def _wrapped_legend_strip(label_low="Less", label_high="More"):
    """Small 'Less -> More' swatch key for the calendar heatmap."""
    dark = st.context.theme.type == 'dark'
    colors = _charts_mod.CAL_HEATMAP_DARK if dark else _charts_mod.CAL_HEATMAP_LIGHT
    text_color = '#9ca3af' if dark else '#888'
    swatches = ''.join(
        f'<span style="display:inline-block;width:12px;height:12px;border-radius:3px;'
        f'background:{c};margin:0 2px"></span>'
        for c in colors
    )
    st.markdown(
        f'<div style="display:flex;align-items:center;justify-content:flex-end;gap:6px;'
        f'font-size:12px;color:{text_color};margin-top:-6px">'
        f'{label_low} {swatches} {label_high}</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Wrapped tab
# ---------------------------------------------------------------------------
def render_wrapped_tab(df, settings, athlete_profile):
    """Wrapped tab — a Spotify-Wrapped-style "reveal" for an arbitrary
    period/sport selection (not just a fixed year like Strava's own Wrapped):
    period + sport + Year/Month breakdown controls, a hero number with a
    prior-period delta, KPI cards, a trend chart + sport-breakdown donut, an
    activity calendar heatmap, PR-flagged records, fun facts, and a longest-
    activities table. Uses the period/sport filter helpers above, so its
    prior-period comparison is period-type-aware (year-over-year, same month
    last year, or trailing-N-days-over-trailing-N-days)."""
    today = date.today()
    current_year = today.year

    # --- Controls ---
    period_options, period_meta = _build_period_options(df)
    c1, c2, c3 = st.columns([2, 2, 1])
    with c1:
        selected_period = st.selectbox(
            "Time period", period_options, index=0, key="wrapped_period",
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

    # --- Compute stats + prior-period comparison ---
    stats = process_data.compute_period_stats(filtered)
    curr = stats['totals']

    prior = _prior_period_filter(df, period_meta[selected_period])
    if prior is not None:
        prior = _filter_by_sport(prior, selected_sport)
    prior_totals = (process_data.compute_period_stats(prior).get('totals')
                    if (prior is not None and not prior.empty) else None)

    # --- Header ---
    who = (athlete_profile['firstname'] + " — ") if athlete_profile.get('firstname') else ""
    st.markdown(f"### {who}{selected_period} · {selected_sport}")

    def _delta(key):
        if not prior_totals:
            return None
        d = curr[key] - prior_totals[key]
        return f"{d:+,.0f}" if abs(d) >= 0.5 else None

    # --- Hero: the one headline number this view leads with ---
    _hero_delta = _delta('miles')
    _wrapped_hero(
        f"{curr['miles']:,.0f} mi",
        "Total Distance",
        caption=(f"{_hero_delta} mi vs the previous comparable period" if _hero_delta else None),
    )

    # --- KPI cards ---
    kpi_items = [
        {'label': 'Activities',      'value': f"{curr['activities']:,}", 'delta': _delta('activities')},
        {'label': 'Hours',           'value': f"{curr['hours']:,.0f}",   'delta': _delta('hours')},
        {'label': 'Elevation (ft)',  'value': f"{curr['vert_ft']:,.0f}", 'delta': _delta('vert_ft')},
    ]
    if athlete_profile.get('follower_count'):
        kpi_items.append({'label': 'Followers', 'value': f"{athlete_profile['follower_count']:,}"})
        kpi_items.append({'label': 'Following', 'value': f"{athlete_profile['friend_count']:,}"})
    _wrapped_cards(kpi_items, cols=len(kpi_items))
    if prior_totals:
        st.caption("▲▼ vs the previous comparable period")

    st.divider()

    # --- Charts: trend + sport breakdown ---
    bucket_df = process_data.bucket_distance_breakdown(filtered)
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
        # A donut needs >= 2 slices to carry sport identity; a single-sport
        # filter falls back to the by-type bar chart (Ride/VirtualRide/…).
        if len(bucket_df) > 1:
            st.plotly_chart(make_sport_breakdown_donut(bucket_df, 'Miles'))
        else:
            st.plotly_chart(
                make_sport_breakdown_chart(stats['sport_breakdown'], 'miles', 'Miles'),
            )

    st.divider()

    # --- Activity calendar ---
    st.subheader("Activity Calendar")
    daily = process_data.build_daily_totals(filtered)
    st.plotly_chart(make_calendar_heatmap(daily, 'Miles'))
    _wrapped_legend_strip()
    _span_days = (filtered['start_date_local'].dt.date.max()
                  - filtered['start_date_local'].dt.date.min()).days + 1
    if _span_days > 371:
        st.caption("Showing the most recent 365 days of the selected period.")

    st.divider()

    # --- Records ---
    st.subheader("Records")
    alltime_sport = _filter_by_sport(df, selected_sport)
    records = process_data.compute_records(filtered, alltime_sport)
    _wrapped_cards([
        {
            'label': rec['label'],
            'value': rec['value'],
            'badge': '🏆' if rec['is_pr'] else None,
        }
        for rec in records
    ], cols=3)
    st.caption("🏆 = all-time best for the current activity filter")

    st.divider()

    # --- Fun Facts ---
    st.subheader("Fun Facts")
    ff = stats['fun_facts']
    _wrapped_cards([
        {'label': 'Everests Climbed', 'value': f"{ff['everests']:.1f}",
         'caption': f"{curr['vert_ft']:,.0f} ft total"},
        {'label': 'Around the Earth', 'value': f"{ff['earth_pct']:.1f}%",
         'caption': f"{curr['miles']:,.0f} miles"},
        {'label': 'Days in Motion',   'value': f"{ff['days_moving']:.1f}",
         'caption': f"{curr['hours']:,.0f} hours total"},
    ], cols=3)

    st.divider()

    # --- Longest activities ---
    _render_longest_table(
        filtered, 'distance_miles',
        lambda r: f"{r['distance_miles']:,.1f} mi",
        "Longest Activities",
    )


# ---------------------------------------------------------------------------
# Trends tab — NOT currently wired to any page/nav entry (no _p_* function or
# _view_pages/_tools_pages listing calls this). Likely an earlier prototype
# of the recent-months comparison idea that the per-tab "Experiments" section
# (render_month_view/render_week_view) superseded. Kept as-is rather than
# deleted since it's still functional if reconnected — flagging here so it
# doesn't read as a live tab.
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
    display = _add_strava_url(display)

    show_cols  = ['Date', 'name', 'final_type', 'Distance', 'Duration', 'Elevation', 'Gear', 'strava_url']
    rename_map = {'name': 'Activity', 'final_type': 'Type', 'strava_url': 'View on Strava'}

    st.dataframe(
        display[show_cols].rename(columns=rename_map),
        hide_index=True,
        column_config=_STRAVA_URL_COL_CONFIG,
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


@st.cache_resource
def _png_export_available():
    """Kaleido needs a Chrome/Chromium binary, which some hosts (e.g.
    Streamlit Community Cloud) don't have. Probe once per process so the
    Export tab can degrade to CSV-only instead of crashing."""
    import plotly.graph_objects as go
    try:
        go.Figure().to_image(format='png', width=8, height=8)
        return True
    except Exception:
        return False


def _png_download_button(fig, name, key):
    """PNG download button, or a short note where PNG rendering isn't possible."""
    if _png_export_available():
        st.download_button(
            f"Download {name}.png", _fig_to_png(fig), f"{name}.png", "image/png",
            key=key,
        )
    else:
        st.caption("PNG export unavailable on this host (no Chrome for kaleido).")


def _to_csv(df):
    return df.to_csv(index=False).encode('utf-8')


def render_export_tab(df, settings):
    """Export tab, four sections: (1) period/sport-filtered activity summary
    — charts + CSV/PNG downloads, reusing the same period/sport filter
    helpers as Wrapped; (2) annual per-sport summary charts over the *full*
    archive, unaffected by section 1's filter; (3) monthly breakdowns for a
    selected year; (4) a "download everything" ZIP bundling every chart PNG
    and table CSV generated above. PNG downloads degrade gracefully via
    _png_download_button when kaleido has no Chrome to render with."""
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
            _png_download_button(fig, name, key=f"dl_png_{name}")

    st.divider()

    act_cols = {
        'start_date_local': 'Date', 'name': 'Activity', 'final_type': 'Type',
        'distance_miles': 'Miles', 'moving_time': 'Moving Time (s)',
        'elevation_feet': 'Elevation (ft)',
    }
    act_df = filtered[['id'] + list(act_cols)].rename(columns=act_cols).copy()
    act_df['Date'] = pd.to_datetime(act_df['Date']).apply(_fmt_date)
    act_df = _add_strava_url(act_df).drop(columns='id').rename(columns={'strava_url': 'View on Strava'})

    sport_df = (
        stats['sport_breakdown'][['final_type', 'activities', 'miles', 'hours', 'vert_ft']]
        .rename(columns={'final_type': 'Sport', 'activities': 'Activities',
                         'miles': 'Miles', 'hours': 'Hours', 'vert_ft': 'Vert (ft)'})
    )

    longest_df = (
        filtered.sort_values(
            ['distance_miles', 'start_date_local'], ascending=[False, False],
        ).head(20)
        [['id', 'start_date_local', 'name', 'final_type', 'distance_miles', 'moving_time']]
        .rename(columns={'start_date_local': 'Date', 'name': 'Activity', 'final_type': 'Type',
                         'distance_miles': 'Miles', 'moving_time': 'Moving Time (s)'})
        .copy()
    )
    longest_df['Date'] = pd.to_datetime(longest_df['Date']).apply(_fmt_date)
    longest_df = _add_strava_url(longest_df).drop(columns='id').rename(columns={'strava_url': 'View on Strava'})

    tables = {
        'activities':         (act_df,     f"All {len(act_df):,} activities in selected period"),
        'sport_summary':      (sport_df,   "Distance and time by sport"),
        'longest_activities': (longest_df, "Top 20 activities by distance"),
    }

    st.subheader("Data Tables")
    for fname, (tdf, caption) in tables.items():
        st.caption(caption)
        col_config = _STRAVA_URL_COL_CONFIG if 'View on Strava' in tdf.columns else None
        st.dataframe(tdf.head(10), width="stretch", hide_index=True, column_config=col_config)
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
            _png_download_button(fig, name, key=f"dl_annual_png_{name}")

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
            _png_download_button(fig, name, key=f"dl_monthly_png_{name}")

    st.divider()

    # ── ZIP: all charts + tables ──────────────────────────────────────────
    st.subheader("Download Everything")
    slug = selected_period.replace(' ', '_').replace('/', '-')
    all_figs = {**summary_figs, **annual_figs, **monthly_figs}
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        if _png_export_available():
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


def _fmt_datetime(dt):
    """Format a Timestamp as 'M/D/YYYY, H:MM AM/PM' without leading zeros."""
    time_str = dt.strftime('%I:%M %p').lstrip('0')
    return f"{_fmt_date(dt)}, {time_str}"


def _most_recent_activity_line(df):
    """One-line summary of the latest logged activity: date/time, sport, distance."""
    if df is None or df.empty:
        return None
    row = df.loc[df['start_date_local'].idxmax()]
    when = _fmt_datetime(row['start_date_local'])
    sport = row.get('final_type') or 'Activity'
    dist = row.get('distance_miles') or 0
    if dist > 0.05:
        return f"Latest: {when} · {sport} · {dist:,.1f} mi"
    return f"Latest: {when} · {sport}"


def render_data_sync(df):
    """Compact data-sync footer in the sidebar. Assumes the caller is already
    inside the sidebar context (the nav block opens it). Keeps the 'Activities
    in archive' count front and center — it's the number that's satisfying to
    watch climb — and trims the rest to a single status line plus the button."""
    st.divider()
    st.markdown("**Data Sync**")

    _recent = _most_recent_activity_line(df)

    if config.DEMO_MODE:
        # Read-only demo build: bundled sanitized dataset, no Strava
        # credentials on the host, so live sync is unavailable by design.
        st.metric("Activities in archive", f"{_archive_count():,}")
        if _recent:
            st.caption(_recent)
        st.caption("Demo mode — read-only sample dataset; live sync is disabled.")
        return

    last = _load_last_sync()
    if last:
        total = last.get('activity_count_latest_fetch', 0)
        st.metric("Activities in archive", f"{total:,}")
        age = _age_string(last.get('last_check', ''))
        new_ct = last.get('new_on_last_sync')
        status = f"Synced {age}"
        if new_ct:
            status += f"  ·  ↑{new_ct} new"
        st.caption(status)
        if _recent:
            st.caption(_recent)
    else:
        st.caption("No sync record yet — run `python run_pipeline.py` once.")

    if st.button("🔄 Sync Now", type="primary", width="stretch"):
        _run_sync()

    years = sorted(set(config.STRAVA_YEARS))
    if years:
        contiguous = years == list(range(years[0], years[-1] + 1))
        span = (f"{years[0]}–{years[-1]}" if contiguous and len(years) > 1
                else ", ".join(str(y) for y in years))
        st.caption(f"Checking {span}")


# ---------------------------------------------------------------------------
# Settings sections (one sidebar page each)
# ---------------------------------------------------------------------------
def _write_settings(new_settings, new_theme, old_theme):
    """Persist the full settings dict, clear the cache, and refresh. Flips the
    theme via JS only when it actually changed; otherwise confirms and reruns."""
    with open(config.SETTINGS_FILE, 'w') as f:
        json.dump(new_settings, f, indent=2)
    load_settings.clear()
    if new_theme != old_theme:
        _apply_theme_js(new_theme)
    else:
        st.success("Settings saved.")
        st.rerun()


def render_settings_section(settings, section):
    """Render one settings section as a standalone sidebar page.

    Each section reads its values straight from saved settings via value=/index=
    (a keyed widget loses its session_state once its page stops rendering, so
    seeding from session is unreliable once the sections are separate pages) and
    has its own Save button that merges just its slice into settings.json.
    Unsaved edits are intentionally dropped when you navigate away.
    """
    import copy
    conv          = settings.get('conversions', {})
    goals         = settings.get('goals', {})
    saved_seasons = settings.get('seasons', {})
    saved_home    = settings.get('home_location', {})
    saved_images  = settings.get('images', {}) or {}
    ref_options   = ["Bike", "Run", "Hike"]
    _bike_mode_options = ['fixed', 'derived']
    _month_nums   = list(range(1, 13))
    _month_names  = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                     'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

    def _save(**section_overrides):
        new = copy.deepcopy(settings)
        new.update(section_overrides)
        _write_settings(new, new.get('theme', 'light'),
                        settings.get('theme', 'light'))

    # ---- Sports ----
    if section == "sports":
        st.subheader("Primary Sport Tabs")
        st.caption(
            "Which sports get a dedicated tab in the View section of the sidebar. "
            "Combined and Wrapped always include every sport regardless of these — "
            "this only controls which sports get their own tab."
        )
        saved_tabs = settings.get('sport_tabs', {})
        tcol_bike, tcol_snow, tcol_swim, tcol_run, tcol_hike = st.columns(5)
        with tcol_bike:
            tab_bike = st.checkbox("Bike", value=saved_tabs.get('bike', True), key="settings_tab_bike")
        with tcol_snow:
            tab_snow = st.checkbox("Snow", value=saved_tabs.get('snow', True), key="settings_tab_snow")
        with tcol_swim:
            tab_swim = st.checkbox("Swim", value=saved_tabs.get('swim', True), key="settings_tab_swim")
        with tcol_run:
            tab_run = st.checkbox("Running", value=saved_tabs.get('run', False), key="settings_tab_run")
        with tcol_hike:
            tab_hike = st.checkbox("Hiking", value=saved_tabs.get('hike', False), key="settings_tab_hike")

        st.divider()
        st.subheader("Why Equity Miles?")
        st.caption(
            "If biking is your primary sport, a lot of your effort naturally gets "
            "measured in miles — but a week of skiing or swimming doesn't produce a "
            "mileage number that means anything next to a bike ride. Equity miles convert "
            "every sport's effort into one common unit (your reference sport below), so "
            "questions like \"was this a better year than last year?\" have a real answer "
            "even when the mix of sports you did changed."
        )

        st.divider()
        st.subheader("Reference Sport")
        st.caption(
            "Equity miles are expressed in units of this sport. "
            "Bike, Run, and Hike all use distance (miles) so they can serve as the "
            "reference. Swim and Ski use different native units (meters / vertical "
            "feet) — their conversion rates below always express how many native "
            "units equal one equity mile."
        )
        _saved_ref = settings.get('reference_sport', 'Bike')
        ref_sport = st.radio(
            "Reference sport", ref_options,
            index=ref_options.index(_saved_ref) if _saved_ref in ref_options else 0,
            horizontal=True,
            key="settings_ref_sport",
        )
        if ref_sport != _saved_ref:
            st.warning(
                f"Reference sport changed from **{_saved_ref}** to **{ref_sport}**. "
                "Review the conversion rates below to make sure they still reflect "
                "your effort equivalences, then hit Save."
            )

        st.divider()
        st.subheader("Equity Mile Conversions")
        st.caption(f"How many native units = 1 {ref_sport} equity mile.")

        dcol_bike, dcol_run, dcol_hike, dcol_paddle = st.columns(4)
        with dcol_bike:
            st.markdown("**Bike**")
            if ref_sport == 'Bike':
                st.caption("Reference"); st.markdown("1 mi = 1 equity mi"); bike_rate = 1.0
            else:
                bike_rate = st.number_input(
                    "miles = 1 equity mi", min_value=0.1, max_value=100.0,
                    step=0.5, format="%.1f", key="settings_bike_rate",
                    value=float(conv.get('bike_miles_per_ref_unit', 1.0)),
                )
        with dcol_run:
            st.markdown("**Run**")
            if ref_sport == 'Run':
                st.caption("Reference"); st.markdown("1 mi = 1 equity mi"); run_rate = 1.0
            else:
                run_rate = st.number_input(
                    "miles = 1 equity mi", min_value=0.1, max_value=100.0,
                    step=0.5, format="%.1f", key="settings_run_rate",
                    value=float(conv.get('run_miles_per_ref_unit', 1.0)),
                )
        with dcol_hike:
            st.markdown("**Hike / Walk**")
            if ref_sport == 'Hike':
                st.caption("Reference"); st.markdown("1 mi = 1 equity mi"); hike_rate = 1.0
            else:
                hike_rate = st.number_input(
                    "miles = 1 equity mi", min_value=0.1, max_value=100.0,
                    step=0.5, format="%.1f", key="settings_hike_rate",
                    value=float(conv.get('hike_miles_per_ref_unit', 3.0)),
                )
        with dcol_paddle:
            st.markdown("**Paddle**")
            paddle_rate = st.number_input(
                "miles = 1 equity mi", min_value=0.1, max_value=100.0,
                step=0.5, format="%.1f", key="settings_paddle_rate",
                value=float(conv.get('paddle_miles_per_ref_unit', 2.0)),
            )

        scol_swim, scol_ski = st.columns(2)
        with scol_swim:
            st.markdown("**Swim**")
            swim_rate = st.number_input(
                "meters = 1 equity mi", min_value=1, max_value=10000, step=10,
                key="settings_swim_rate",
                value=int(conv.get('swim_meters_per_ref_unit', conv.get('swim_meters_per_mile', 100))),
            )
        with scol_ski:
            st.markdown("**Ski**")
            ski_rate = st.number_input(
                "vertical feet = 1 equity mi", min_value=100, max_value=100000, step=100,
                key="settings_ski_rate",
                value=int(conv.get('ski_vert_per_ref_unit', conv.get('ski_vert_per_mile', 1000))),
            )

        st.divider()
        if st.button("Save settings", type="primary"):
            _save(
                reference_sport=ref_sport,
                sport_tabs={
                    'bike': tab_bike,
                    'snow': tab_snow,
                    'swim': tab_swim,
                    'run':  tab_run,
                    'hike': tab_hike,
                },
                conversions={
                    'bike_miles_per_ref_unit':   bike_rate,
                    'run_miles_per_ref_unit':    run_rate,
                    'hike_miles_per_ref_unit':   hike_rate,
                    'paddle_miles_per_ref_unit': paddle_rate,
                    'swim_meters_per_ref_unit':  swim_rate,
                    'ski_vert_per_ref_unit':     ski_rate,
                },
            )

    # ---- Goals ----
    elif section == "goals":
        st.subheader("Goals")
        st.markdown("**Equity Miles**")
        gcol_annual, gcol_monthly = st.columns(2)
        with gcol_annual:
            annual_eq = st.number_input(
                "Annual equity miles", min_value=0, max_value=100000, step=100,
                key="settings_annual_eq",
                value=int(goals.get('annual_equity_miles', 3000)),
            )
        with gcol_monthly:
            monthly_eq = st.number_input(
                "Monthly equity miles", min_value=0, max_value=10000, step=10,
                key="settings_monthly_eq",
                value=int(goals.get('monthly_equity_miles', 250)),
            )

        st.markdown("**Sport-Specific**")
        gcol_ski, gcol_swim = st.columns(2)
        with gcol_ski:
            ski_goal = st.number_input(
                "Ski season vertical feet (cumulative)",
                min_value=0, max_value=10000000, step=10000,
                key="settings_ski_goal",
                value=int(goals.get('ski_season_vert_ft', 200000)),
            )
        with gcol_swim:
            swim_goal = st.number_input(
                "Swim monthly meters", min_value=0, max_value=1000000, step=500,
                key="settings_swim_goal",
                value=int(goals.get('swim_monthly_meters', 10000)),
            )

        st.markdown("**Bike Monthly Miles**")
        _saved_mode = goals.get('bike_monthly_mode', 'derived')
        bike_mode = st.radio(
            "Goal mode", _bike_mode_options,
            index=_bike_mode_options.index(_saved_mode) if _saved_mode in _bike_mode_options else 1,
            horizontal=True,
            format_func=lambda m: 'Fixed value' if m == 'fixed' else 'Derived from total target',
            key="settings_bike_monthly_mode",
        )
        bike_monthly_miles  = float(goals.get('bike_monthly_miles', 150))
        monthly_total_target = float(goals.get('monthly_total_target_miles', 200))
        if bike_mode == 'fixed':
            bike_monthly_miles = st.number_input(
                "Bike monthly miles", min_value=0.0, max_value=10000.0,
                step=10.0, format="%.0f",
                key="settings_bike_monthly_miles", value=bike_monthly_miles,
            )
        else:
            monthly_total_target = st.number_input(
                "Monthly total target (bike miles)",
                min_value=0.0, max_value=10000.0, step=10.0, format="%.0f",
                key="settings_monthly_total_target", value=monthly_total_target,
            )
            st.caption(
                "Each month's bike target = total − swim equivalent (swim-season months) "
                "− ski equivalent (ski-season months, spread evenly). Equivalents use the "
                "conversion rates and season months from the other Settings pages."
            )
            preview = process_data.bike_monthly_goal_series({
                **settings,
                'goals': {
                    **goals,
                    'monthly_total_target_miles': monthly_total_target,
                    'ski_season_vert_ft':         ski_goal,
                    'swim_monthly_meters':        swim_goal,
                },
            })
            st.dataframe(
                {'Month': _month_names, 'Bike target (mi)': [round(v) for v in preview]},
                hide_index=True, width="stretch",
            )

        st.divider()
        if st.button("Save settings", type="primary"):
            _save(goals={
                'annual_equity_miles':        annual_eq,
                'monthly_equity_miles':       monthly_eq,
                'ski_season_vert_ft':         ski_goal,
                'swim_monthly_meters':        swim_goal,
                'monthly_total_target_miles': monthly_total_target,
                'bike_monthly_mode':          bike_mode,
                'bike_monthly_miles':         bike_monthly_miles,
            })

    # ---- Seasons ----
    elif section == "seasons":
        st.subheader("Season Months")
        st.caption("Controls which months appear in the monthly chart on each sport tab.")

        def _month_index(field, default):
            m = saved_seasons.get(field, default)
            return _month_nums.index(m) if m in _month_nums else default - 1

        smcol_ski, smcol_swim = st.columns(2)
        with smcol_ski:
            st.markdown("**Ski season**")
            ski_start = st.selectbox(
                "Start month", _month_nums, index=_month_index('ski_start_month', 11),
                format_func=lambda m: _month_names[m - 1], key="settings_ski_start_month",
            )
            ski_end = st.selectbox(
                "End month", _month_nums, index=_month_index('ski_end_month', 5),
                format_func=lambda m: _month_names[m - 1], key="settings_ski_end_month",
            )
        with smcol_swim:
            st.markdown("**Swim season**")
            swim_start = st.selectbox(
                "Start month", _month_nums, index=_month_index('swim_start_month', 5),
                format_func=lambda m: _month_names[m - 1], key="settings_swim_start_month",
            )
            swim_end = st.selectbox(
                "End month", _month_nums, index=_month_index('swim_end_month', 9),
                format_func=lambda m: _month_names[m - 1], key="settings_swim_end_month",
            )

        st.divider()
        if st.button("Save settings", type="primary"):
            _save(seasons={
                'ski_start_month':  ski_start,
                'ski_end_month':    ski_end,
                'swim_start_month': swim_start,
                'swim_end_month':   swim_end,
            })

    # ---- Map ----
    elif section == "map":
        st.subheader("Home Location")
        st.caption(
            "When enabled, the bike heatmap centers on your home location instead of "
            "the median of your ride start points."
        )
        home_enabled = st.checkbox(
            "Use custom home location for heatmap", key="settings_home_enabled",
            value=bool(saved_home.get('enabled', False)),
        )
        home_lat = float(saved_home['lat']) if saved_home.get('lat') is not None else 40.0
        home_lon = float(saved_home['lon']) if saved_home.get('lon') is not None else -105.0
        if home_enabled:
            hm_col_lat, hm_col_lon = st.columns(2)
            with hm_col_lat:
                home_lat = st.number_input(
                    "Latitude", min_value=-90.0, max_value=90.0,
                    step=0.0001, format="%.6f", key="settings_home_lat", value=home_lat,
                )
            with hm_col_lon:
                home_lon = st.number_input(
                    "Longitude", min_value=-180.0, max_value=180.0,
                    step=0.0001, format="%.6f", key="settings_home_lon", value=home_lon,
                )
            st.caption("Tip: right-click any location in Google Maps → 'What's here?' to copy coordinates.")

        st.divider()
        if st.button("Save settings", type="primary"):
            _save(home_location={
                'enabled': home_enabled,
                'lat': home_lat if home_enabled else None,
                'lon': home_lon if home_enabled else None,
            })

    # ---- Appearance ----
    elif section == "appearance":
        _theme_opts = ['dark', 'light']
        _saved_theme = settings.get('theme', 'light')
        st.subheader("Theme")
        theme = st.radio(
            "Appearance", _theme_opts,
            index=_theme_opts.index(_saved_theme) if _saved_theme in _theme_opts else 1,
            horizontal=True,
            format_func=lambda x: 'Dark' if x == 'dark' else 'Light',
            key='settings_theme',
        )
        st.caption("Takes effect immediately on save.")

        st.divider()
        st.subheader("Sport tab images")
        st.caption(
            "Each sport tab shows a default image beside its all-time stats — a bundled "
            "photo for Snow and Swim, or the auto-generated route heatmap for Bike. Set a "
            "path here to override any of them with your own image."
        )
        _img_col_bike, _img_col_snow, _img_col_swim = st.columns(3)
        with _img_col_bike:
            bike_path = st.text_input(
                "Bike image path (blank = route heatmap)",
                key="settings_bike_image_path", value=saved_images.get('bike_path') or '',
                placeholder=config.BIKE_DEFAULT_IMAGE,
            )
            _bike_preview = bike_path or config.BIKE_DEFAULT_IMAGE
            if os.path.exists(_bike_preview):
                st.image(_bike_preview, width=180)
            else:
                st.caption(f"⚠ File not found: {_bike_preview}")
        with _img_col_snow:
            snow_path = st.text_input(
                "Snow image path (blank = default)",
                key="settings_snow_image_path", value=saved_images.get('snow_path') or '',
                placeholder=config.SNOW_DEFAULT_IMAGE,
            )
            _snow_preview = snow_path or config.SNOW_DEFAULT_IMAGE
            if os.path.exists(_snow_preview):
                st.image(_snow_preview, width=180)
            else:
                st.caption(f"⚠ File not found: {_snow_preview}")
        with _img_col_swim:
            swim_path = st.text_input(
                "Swim image path (blank = default)",
                key="settings_swim_image_path", value=saved_images.get('swim_path') or '',
                placeholder=config.SWIM_DEFAULT_IMAGE,
            )
            _swim_preview = swim_path or config.SWIM_DEFAULT_IMAGE
            if os.path.exists(_swim_preview):
                st.image(_swim_preview, width=180)
            else:
                st.caption(f"⚠ File not found: {_swim_preview}")

        st.divider()
        if st.button("Save settings", type="primary"):
            _save(
                theme=theme,
                images={
                    'bike_path': (bike_path or '').strip() or None,
                    'snow_path': (snow_path or '').strip() or None,
                    'swim_path': (swim_path or '').strip() or None,
                },
            )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
df = load_activities()
gear_map = load_gear_map()
settings = load_settings()
athlete_profile = load_athlete_profile()

if df.empty:
    st.error("No activity data found. Run the pipeline first.")
    st.stop()

# Eq-named activities are manual equity declarations (no GPS, distance = declared
# equity miles) — exclude them from every sport tab so they don't corrupt real
# metrics. Their equity is handled in the Combined tab via reconcile_equity_declarations.
_eq_mask = df['name'].str.match(process_data._EQ_PATTERN, na=False)
bike_df  = df[df['final_type'].isin(BIKE_TYPES)  & ~_eq_mask].copy()
ski_df   = df[df['final_type'].isin(SKI_TYPES)   & ~_eq_mask].copy()
swim_df  = df[df['final_type'].isin(SWIM_TYPES)  & ~_eq_mask].copy()
run_df   = df[df['final_type'].isin(RUN_TYPES)   & ~_eq_mask].copy()
hike_df  = df[df['final_type'].isin(HIKE_TYPES)  & ~_eq_mask].copy()

# On first render of each browser session, sync localStorage to the saved theme
# preference.  Subsequent renders skip this (initial_theme_synced is set) so
# that the user can still override via Streamlit's native ⋮ Settings menu.
if not st.session_state.get('initial_theme_synced'):
    st.session_state['initial_theme_synced'] = True
    _saved_theme = settings.get('theme', 'light')
    _current_theme = st.context.theme.type or 'light'
    if _saved_theme != _current_theme:
        _apply_theme_js(_saved_theme)

# ---------------------------------------------------------------------------
# Sidebar navigation — native st.navigation with a hand-built sidebar, mirroring
# the spotify-stats layout. Page selection survives reruns (no tab snap-back),
# and routing guarantees exactly one active item across all groups. Each page is
# a zero-arg callable that closes over the module-level frames loaded above.
# TODO: Wrapped stays in View for now; revisit its contents to make it richer.
# ---------------------------------------------------------------------------
def _p_bike():     render_bike_tab(bike_df, gear_map, settings)
def _p_snow():     render_ski_tab(ski_df, settings)
def _p_swim():     render_swim_tab(swim_df, settings, df)
def _p_run():      render_activity_tab(run_df, gear_map, settings, sport_key='run', label='Running',
                                        color=RUN_PURPLE, color_light=RUN_PURPLE_LIGHT,
                                        count_noun='Runs', gear_noun='Shoes', ref_label='Run')
def _p_hike():     render_activity_tab(hike_df, gear_map, settings, sport_key='hike', label='Hiking',
                                        color=HIKE_GREEN, color_light=HIKE_GREEN_LIGHT,
                                        count_noun='Hikes', gear_noun='Shoes', ref_label='Hike')
def _p_combined(): render_equity_tab(df, settings)
def _p_wrapped():  render_wrapped_tab(df, settings, athlete_profile)
def _p_explore():  render_explore_tab(df, gear_map)
def _p_export():   render_export_tab(df, settings)
def _p_set_sports():  render_settings_section(settings, "sports")
def _p_set_goals():   render_settings_section(settings, "goals")
def _p_set_seasons(): render_settings_section(settings, "seasons")
def _p_set_map():     render_settings_section(settings, "map")
def _p_set_appear():  render_settings_section(settings, "appearance")

# Which sport tabs are enabled, in the fixed View order — Combined/Wrapped
# always show every sport regardless of this and are appended separately.
_sport_tab_settings = settings.get('sport_tabs', {})
_sport_page_specs = [
    ('bike', _p_bike, "Bike",    "🚴", "bike"),
    ('snow', _p_snow, "Snow",    "⛷️", "snow"),
    ('swim', _p_swim, "Swim",    "🏊", "swim"),
    ('run',  _p_run,  "Running", "🏃", "run"),
    ('hike', _p_hike, "Hiking",  "🥾", "hike"),
]
_enabled_sport_specs = [s for s in _sport_page_specs if _sport_tab_settings.get(s[0], False)]
_enabled_sport_paths = [s[4] for s in _enabled_sport_specs]

# Open on the reference sport's view when it has a dedicated tab enabled;
# otherwise the first enabled sport tab; otherwise Combined.
_ref_path = {"Bike": "bike", "Run": "run", "Hike": "hike"}.get(settings.get('reference_sport', 'Bike'))
if _ref_path in _enabled_sport_paths:
    _default_path = _ref_path
elif _enabled_sport_paths:
    _default_path = _enabled_sport_paths[0]
else:
    _default_path = "combined"

def _page(fn, title, icon, path):
    return st.Page(fn, title=title, icon=icon, url_path=path,
                   default=(path == _default_path))

_view_pages = [
    _page(fn, title, icon, path) for _, fn, title, icon, path in _enabled_sport_specs
] + [
    _page(_p_combined, "Combined", "➕", "combined"),
    _page(_p_wrapped,  "Wrapped",  "🎁", "wrapped"),
]
_tools_pages = [
    _page(_p_explore, "Explore", "🔍", "explore"),
    _page(_p_export,  "Export",  "📤", "export"),
]
# Settings expands into one page per section, listed independently in the
# sidebar (iconless, to match the View/Tools sports' visual grouping).
_settings_pages = [
    _page(_p_set_sports,  "Sport types and equity", None, "settings-sport"),
    _page(_p_set_goals,   "Goals",        None, "settings-goals"),
    _page(_p_set_seasons, "Seasons",      None, "settings-seasons"),
    _page(_p_set_map,     "Map",          None, "settings-map"),
    _page(_p_set_appear,  "Appearance",   None, "settings-appearance"),
]

pg = st.navigation(
    {"View": _view_pages, "Tools": _tools_pages, "Settings": _settings_pages},
    position="hidden",
)

with st.sidebar:
    _sidebar_dark = st.context.theme.type == 'dark'
    _charts_mod.set_theme(_sidebar_dark)
    _sidebar_bg = '#262730' if _sidebar_dark else '#f0f2f6'
    # Streamlit's native sidebar open/close buttons default to a ~28px hit
    # target — fiddly to tap precisely on a phone. Enlarge both toward the
    # ~44px mobile touch-target guideline; purely cosmetic/hit-area, no
    # behavior change. The header (holding the close button) also isn't
    # sticky by default — on a phone, scrolling down through View/Data
    # Sync/Settings/Tools pushes the close button off the top of the screen
    # entirely, with no way to close the sidebar without scrolling back up
    # first. Pin it to the top of the sidebar's own scroll area instead.
    st.markdown(
        f"""
        <style>
        [data-testid="stSidebarCollapseButton"] button,
        [data-testid="stExpandSidebarButton"] {{
            width: 44px !important;
            height: 44px !important;
            padding: 8px !important;
        }}
        [data-testid="stSidebarCollapseButton"] [data-testid="stIconMaterial"],
        [data-testid="stExpandSidebarButton"] [data-testid="stIconMaterial"] {{
            font-size: 28px !important;
        }}
        [data-testid="stSidebarHeader"] {{
            position: sticky;
            top: 0;
            z-index: 999;
            background-color: {_sidebar_bg};
        }}
        /* Page-link labels default to a single non-wrapping line (e.g. "Sport
           types and equity"), which forces the sidebar to stay wide on a
           phone to avoid clipping it. Let labels wrap to a second line
           instead, so the sidebar can be narrowed without losing text. */
        [data-testid="stSidebar"] a[data-testid="stPageLink-NavLink"] {{
            height: auto !important;
            min-height: 32px;
            padding-top: 6px !important;
            padding-bottom: 6px !important;
        }}
        [data-testid="stSidebar"] a[data-testid="stPageLink-NavLink"] > span:last-child {{
            height: auto !important;
            overflow: visible !important;
            white-space: normal !important;
        }}
        [data-testid="stSidebar"] a[data-testid="stPageLink-NavLink"] p {{
            white-space: normal !important;
            word-break: break-word;
            height: auto !important;
            line-height: 1.25;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        "<h2 style='margin:0 0 0.2rem 0;color:#FC4C02'>Equity Miles</h2>"
        "<a href='https://github.com/jimmoffitt/strava-stats' target='_blank' "
        "style='font-size:13px;text-decoration:none;color:inherit;opacity:0.75'>"
        "&#128279; View source on GitHub</a>",
        unsafe_allow_html=True,
    )
    st.markdown("<div style='margin-top:0.75rem'></div>", unsafe_allow_html=True)
    st.markdown("**View**")
    for _p in _view_pages:
        st.page_link(_p)
    render_data_sync(df)
    st.divider()
    st.markdown("**Settings**")
    for _p in _settings_pages:
        st.page_link(_p)
    st.divider()
    st.markdown("**Tools**")
    for _p in _tools_pages:
        st.page_link(_p)

    # Selecting a page link on mobile leaves the sidebar covering the whole
    # screen with no obvious next step. Auto-collapse it after navigation,
    # matching typical mobile nav-drawer behavior; desktop is left alone
    # since the sidebar coexists with the content there.
    _components.html(
        """
        <script>
        (function() {
            if (window.parent.__eqmSidebarAutoCloseAttached) return;
            window.parent.__eqmSidebarAutoCloseAttached = true;
            window.parent.document.addEventListener('click', function(e) {
                var link = e.target.closest('[data-testid="stPageLink-NavLink"]');
                if (!link) return;
                if (window.parent.innerWidth > 768) return;
                setTimeout(function() {
                    var doc = window.parent.document;
                    var sidebar = doc.querySelector('[data-testid="stSidebar"]');
                    var btn = doc.querySelector('[data-testid="stSidebarCollapseButton"] button');
                    if (btn && sidebar && sidebar.getAttribute('aria-expanded') === 'true') {
                        btn.click();
                    }
                }, 150);
            }, true);
        })();
        </script>
        """,
        height=0,
        scrolling=False,
    )

pg.run()
