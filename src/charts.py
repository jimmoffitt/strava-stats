"""
src/charts.py — Plotly figure factories for the Streamlit dashboard.

Every public function accepts a DataFrame (or simple arrays) and returns a
go.Figure — no Streamlit calls, making them independently testable and
reusable in gen_screenshots.py. Call set_theme(dark=True/False) once per
render cycle (in the sidebar) to switch all subsequent figures between dark
and light palettes via module-level helpers (_plot_bg, _font_color, etc.).
_base_layout() is the shared layout builder used by every chart function.
"""

import plotly.graph_objects as go

STRAVA_ORANGE = '#FC4C02'
STRAVA_ORANGE_LIGHT = '#FCAB87'
PRIOR_BLUE = 'rgba(70, 130, 200, 0.5)'
SHADOW_GRAY = 'rgba(150, 150, 150, 0.25)'

# Equity sport palette
RUN_PURPLE    = '#8B5CF6'
HIKE_GREEN    = '#22C55E'
PADDLE_AMBER  = '#F59E0B'   # amber — distinct from swim teal and ski blue
CUSTOM_GRAY   = '#9CA3AF'
SWIM_TEAL     = '#00B4D8'
SKI_BLUE      = '#1D4ED8'

# Fixed hue order (never cycled) — the CVD-safety mechanism for every chart
# that breaks activities down by sport. Must match process_data._SPORT_BUCKETS.
_SPORT_COLORS = [
    ('bike',   STRAVA_ORANGE, 'Bike'),
    ('swim',   SWIM_TEAL,     'Swim'),
    ('ski',    SKI_BLUE,      'Ski'),
    ('run',    RUN_PURPLE,    'Run'),
    ('hike',   HIKE_GREEN,    'Hike'),
    ('paddle', PADDLE_AMBER,  'Paddle'),
    ('custom', CUSTOM_GRAY,   'Custom'),
]

# Calendar-heatmap sequential ramp (orange, single-hue light->dark), 0=no
# activity (neutral, not part of the hue ramp) .. 3=highest tercile among
# active days. Validated with scripts/validate_palette.js --ordinal: light-end
# contrast >= 2:1 and adjacent step gaps all clear, both modes.
CAL_HEATMAP_LIGHT = ['#eeeeee', '#fd976c', '#fc6728', '#ca3d02']
CAL_HEATMAP_DARK  = ['#2a2d35', '#b43d0d', '#d64408', '#FC4C02']

# ---------------------------------------------------------------------------
# Theme system — call set_theme(dark) once per Streamlit render cycle from
# render_sync_sidebar(); all chart functions read _dark automatically.
# ---------------------------------------------------------------------------
_dark = False


def set_theme(dark: bool) -> None:
    global _dark
    _dark = dark


def _plot_bg():
    return '#1a1c24' if _dark else 'white'


def _paper_bg():
    return '#0e1117' if _dark else 'white'


def _grid_color():
    return 'rgba(255,255,255,0.10)' if _dark else '#eeeeee'


def _font_color():
    return '#e8e8e8' if _dark else '#31333F'


def _base_layout(**kwargs) -> dict:
    """Common layout keys for every chart; callers merge in chart-specific keys."""
    base = dict(
        plot_bgcolor=_plot_bg(),
        paper_bgcolor=_paper_bg(),
        font=dict(color=_font_color()),
        margin=dict(t=50, b=40, l=40, r=20),
    )
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# Chart factories
# ---------------------------------------------------------------------------

def make_year_dist_chart(yearly_df, dist_col, dist_label, current_year, height=None):
    """
    Bar chart of distance by year.
    Current year bar uses light tint (YTD); prior years use Strava orange.
    Pass height (px) for a thin/compact variant.
    """
    colors = [
        STRAVA_ORANGE_LIGHT if int(row['year']) >= current_year else STRAVA_ORANGE
        for _, row in yearly_df.iterrows()
    ]

    fig = go.Figure(go.Bar(
        x=yearly_df['year'].astype(str),
        y=yearly_df[dist_col],
        marker_color=colors,
        text=[f"{v:,.0f}" for v in yearly_df[dist_col]],
        textposition='outside',
    ))

    # Use integer category index for x — string year values break kaleido rendering.
    year_list = yearly_df['year'].tolist()
    ytd_rows = yearly_df[yearly_df['year'] >= current_year]
    for _, row in ytd_rows.iterrows():
        fig.add_annotation(
            x=year_list.index(row['year']),
            y=row[dist_col],
            text="YTD",
            showarrow=False,
            yshift=28,
            font=dict(size=10, color=STRAVA_ORANGE_LIGHT),
        )

    layout = _base_layout(
        title=f"Annual Distance ({dist_label})",
        xaxis_title="Year",
        yaxis_title=dist_label,
        showlegend=False,
    )
    if height:
        layout['height'] = height
    fig.update_layout(**layout)
    fig.update_xaxes(type='category')
    fig.update_yaxes(gridcolor=_grid_color(), range=[0, yearly_df[dist_col].max() * 1.25])
    return fig


def make_year_time_chart(yearly_df, current_year, height=220):
    """
    Bar chart of riding hours by year.
    Current year bar uses light tint (YTD); prior years use Strava orange.
    """
    colors = [
        STRAVA_ORANGE_LIGHT if int(row['year']) >= current_year else STRAVA_ORANGE
        for _, row in yearly_df.iterrows()
    ]

    fig = go.Figure(go.Bar(
        x=yearly_df['year'].astype(str),
        y=yearly_df['hours'],
        marker_color=colors,
        text=[f"{v:,.1f}" for v in yearly_df['hours']],
        textposition='outside',
    ))

    year_list = yearly_df['year'].tolist()
    ytd_rows = yearly_df[yearly_df['year'] >= current_year]
    for _, row in ytd_rows.iterrows():
        fig.add_annotation(
            x=year_list.index(row['year']),
            y=row['hours'],
            text="YTD",
            showarrow=False,
            yshift=28,
            font=dict(size=10, color=STRAVA_ORANGE_LIGHT),
        )

    layout = _base_layout(
        title="Annual Riding Hours",
        xaxis_title="Year",
        yaxis_title="Hours",
        showlegend=False,
    )
    layout['height'] = height
    fig.update_layout(**layout)
    fig.update_xaxes(type='category')
    fig.update_yaxes(gridcolor=_grid_color())
    return fig


def make_period_comparison_chart(
    ref_df, prior_df, shadow_df,
    x_col, x_label, dist_col, dist_label, title,
    ref_color=None,
):
    """
    Overlay bar chart comparing up to three periods.
    - ref_df   : reference period (solid `ref_color`, defaults to Strava orange)
    - prior_df : same period prior year (blue, 50% opacity)
    - shadow_df: current in-progress period (gray, 25% opacity); None to omit

    All DataFrames must have columns: x_col, dist_col.
    """
    fig = go.Figure()

    if shadow_df is not None and not shadow_df.empty:
        fig.add_trace(go.Bar(
            x=shadow_df[x_col],
            y=shadow_df[dist_col],
            name='Current (in progress)',
            marker_color=SHADOW_GRAY,
            marker_line_width=0,
        ))

    if prior_df is not None and not prior_df.empty:
        fig.add_trace(go.Bar(
            x=prior_df[x_col],
            y=prior_df[dist_col],
            name='Prior year',
            marker_color=PRIOR_BLUE,
        ))

    if ref_df is not None and not ref_df.empty:
        fig.add_trace(go.Bar(
            x=ref_df[x_col],
            y=ref_df[dist_col],
            name='Selected period',
            marker_color=ref_color or STRAVA_ORANGE,
        ))

    fig.update_layout(**_base_layout(
        title=title,
        xaxis_title=x_label,
        yaxis_title=dist_label,
        barmode='overlay',
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1, traceorder='normal'),
    ))
    fig.update_yaxes(gridcolor=_grid_color())
    return fig


def make_monthly_chart(monthly_df, dist_col, dist_label, goal=None, color=None, title=None, height=280):
    """Bar chart of distance by month — 12 bars, 0-filled for empty months.
    Optional dashed goal line. `goal` may be a scalar (one horizontal line) or
    a sequence of 12 values for a per-month stepped goal. `color` overrides
    the bar color (defaults to Strava orange). `title` overrides the default
    "Distance by Month (<unit>)" heading — pass a single space to suppress it
    when the title is being shown elsewhere (e.g. a header row above the
    chart). `height` (px) for a thin/compact variant."""
    bar_color = color or STRAVA_ORANGE
    fig = go.Figure(go.Bar(
        x=monthly_df['month_name'],
        y=monthly_df[dist_col],
        marker_color=bar_color,
        text=[f"{v:,.0f}" if v > 0 else "" for v in monthly_df[dist_col]],
        textposition='inside',
        insidetextanchor='end',
    ))
    if hasattr(goal, '__len__') and not isinstance(goal, str):
        goal_vals = list(goal)
        if len(goal_vals) == len(monthly_df) and any(v > 0 for v in goal_vals):
            fig.add_trace(go.Scatter(
                x=monthly_df['month_name'],
                y=goal_vals,
                mode='lines',
                line=dict(dash='dash', color='gray', width=1.5, shape='hv'),
                name='Goal',
                hovertemplate='Goal: %{y:,.0f}<extra></extra>',
                showlegend=False,
            ))
    elif goal and goal > 0:
        fig.add_hline(
            y=goal,
            line_dash='dash',
            line_color='gray',
            line_width=1.5,
            annotation_text=f"Goal: {goal:,.0f}",
            annotation_position='top right',
            annotation_font_size=11,
        )
    fig.update_layout(**_base_layout(
        title=title or f"Distance by Month ({dist_label})",
        xaxis_title="Month",
        yaxis_title=dist_label,
        showlegend=False,
        height=height,
    ))
    fig.update_yaxes(gridcolor=_grid_color())
    return fig


def make_sport_breakdown_chart(sport_df, dist_col, dist_label):
    """Horizontal bar chart of distance by sport type, sorted ascending."""
    sorted_df = sport_df.sort_values(dist_col, ascending=True)
    fig = go.Figure(go.Bar(
        x=sorted_df[dist_col],
        y=sorted_df['final_type'],
        orientation='h',
        marker_color=STRAVA_ORANGE,
        text=[f"{v:,.0f}" for v in sorted_df[dist_col]],
        textposition='outside',
    ))
    fig.update_layout(**_base_layout(
        title=f"Distance by Sport ({dist_label})",
        xaxis_title=dist_label,
        margin=dict(t=50, b=40, l=60, r=60),
        showlegend=False,
    ))
    fig.update_xaxes(gridcolor=_grid_color())
    return fig


def make_sport_breakdown_donut(bucket_df, value_label, height=None):
    """Donut chart of distance broken into the app's fixed sport-color
    buckets (see process_data.bucket_distance_breakdown) — a part-to-whole
    view, so color has to carry sport identity. Fixed hue order (never
    resorted by value); wedges get a surface-color ring to separate them
    (the pie/donut equivalent of the bar-chart surface gap) and outside
    percent labels so identity never depends on color alone."""
    color_map = {key: color for key, color, _ in _SPORT_COLORS}
    colors = [color_map.get(b, CUSTOM_GRAY) for b in bucket_df['bucket']]

    fig = go.Figure(go.Pie(
        labels=bucket_df['label'],
        values=bucket_df['miles'],
        hole=0.55,
        sort=False,
        marker=dict(colors=colors, line=dict(color=_paper_bg(), width=2)),
        textinfo='percent',
        textposition='outside',
        hovertemplate='%{label}: %{value:,.0f} ' + value_label.lower() + ' (%{percent})<extra></extra>',
    ))
    layout = _base_layout(
        title=f"Distance by Sport ({value_label})",
        showlegend=True,
        legend=dict(orientation='h', yanchor='top', y=-0.05, xanchor='center', x=0.5),
    )
    if height:
        layout['height'] = height
    fig.update_layout(**layout)
    return fig


SWIM_TEAL_LIGHT = '#90E0EF'


def make_swim_year_chart(yearly_df, current_year, annual_goal=None, height=None):
    """
    Bar chart of total meters (or yards) per year.
    Current year lighter; optional dashed annual-goal line.
    Pass height (px) for a thin/compact variant.
    """
    colors = [
        SWIM_TEAL_LIGHT if int(row['year']) >= current_year else SWIM_TEAL
        for _, row in yearly_df.iterrows()
    ]
    y_col = yearly_df.columns[2]  # 'meters' or 'yards' — whichever caller passes

    fig = go.Figure(go.Bar(
        x=yearly_df['year'].astype(str),
        y=yearly_df[y_col],
        marker_color=colors,
        text=[f"{v:,.0f}" for v in yearly_df[y_col]],
        textposition='outside',
    ))

    year_list = yearly_df['year'].tolist()
    ytd_rows = yearly_df[yearly_df['year'] >= current_year]
    for _, row in ytd_rows.iterrows():
        fig.add_annotation(
            x=year_list.index(row['year']), y=row[y_col],
            text="YTD", showarrow=False, yshift=28,
            font=dict(size=10, color=SWIM_TEAL_LIGHT),
        )

    if annual_goal and annual_goal > 0:
        fig.add_hline(
            y=annual_goal,
            line_dash='dash', line_color='gray', line_width=1.5,
            annotation_text=f"Annual goal: {annual_goal:,.0f}",
            annotation_position='top right', annotation_font_size=11,
        )

    layout = _base_layout(
        title=f"Annual Distance ({y_col})",
        xaxis_title="Year",
        yaxis_title=y_col.capitalize(),
        showlegend=False,
    )
    if height:
        layout['height'] = height
    fig.update_layout(**layout)
    fig.update_xaxes(type='category')
    y_max = max(yearly_df[y_col].max() * 1.25, annual_goal * 1.05 if annual_goal else 0)
    fig.update_yaxes(gridcolor=_grid_color(), range=[0, y_max])
    return fig


SKI_BLUE_LIGHT = '#93C5FD'


def make_season_vert_chart(seasonal_df, current_season_key, goal_vert=None, height=None, title=None):
    """
    Bar chart of total vertical feet by ski season.
    Current/in-progress season shown in lighter blue; past seasons in solid blue.
    Optional dashed goal line. Pass height (px) for a thin/compact variant.
    `title` overrides the default "Season Vertical Feet" heading — pass a
    single space to suppress it when shown elsewhere (e.g. a header row).
    """
    colors = [
        SKI_BLUE_LIGHT if int(row['season_key']) >= current_season_key else SKI_BLUE
        for _, row in seasonal_df.iterrows()
    ]

    has_day_stats = 'max_vert_day' in seasonal_df.columns and 'avg_vert_day' in seasonal_df.columns
    if has_day_stats:
        customdata = list(zip(seasonal_df['max_vert_day'], seasonal_df['avg_vert_day']))
        hovertemplate = (
            "<b>%{x}</b><br>"
            "Total: %{y:,.0f} ft<br>"
            "Max day: %{customdata[0]:,.0f} ft<br>"
            "Avg day: %{customdata[1]:,.0f} ft"
            "<extra></extra>"
        )
    else:
        customdata = None
        hovertemplate = None

    bar_kwargs = dict(
        x=seasonal_df['season_label'],
        y=seasonal_df['vert_ft'],
        marker_color=colors,
        text=[f"{v:,.0f}" for v in seasonal_df['vert_ft']],
        textposition='outside',
    )
    if customdata is not None:
        bar_kwargs['customdata'] = customdata
        bar_kwargs['hovertemplate'] = hovertemplate

    fig = go.Figure(go.Bar(**bar_kwargs))

    current_rows = seasonal_df[seasonal_df['season_key'] >= current_season_key]
    for _, row in current_rows.iterrows():
        fig.add_annotation(
            x=row['season_label'],
            y=row['vert_ft'],
            text="YTD",
            showarrow=False,
            yshift=28,
            font=dict(size=10, color=SKI_BLUE_LIGHT),
        )

    if goal_vert and goal_vert > 0:
        fig.add_hline(
            y=goal_vert,
            line_dash='dash',
            line_color='gray',
            line_width=1.5,
            annotation_text=f"Goal: {goal_vert:,} ft",
            annotation_position='top right',
            annotation_font_size=11,
        )

    layout = _base_layout(
        title=title or "Season Vertical Feet",
        xaxis_title="Season",
        yaxis_title="Vertical Feet",
        showlegend=False,
    )
    if height:
        layout['height'] = height
    fig.update_layout(**layout)
    y_max = max(seasonal_df['vert_ft'].max() * 1.25, goal_vert * 1.05 if goal_vert else 0)
    fig.update_yaxes(gridcolor=_grid_color(), range=[0, y_max])
    return fig


def make_equity_annual_chart(equity_df, current_year, ref_label='Bike', height=None):
    """
    Stacked bar chart of equity miles per year broken down by sport.
    Only adds traces for sports that have at least one non-zero value.
    Total label annotated above each bar; current year marked YTD.
    Pass height (px) for a compact/thin variant.
    """
    fig = go.Figure()

    x = equity_df['year'].astype(str)
    for col, color, label in _SPORT_COLORS:
        if col in equity_df.columns and equity_df[col].sum() > 0:
            fig.add_trace(go.Bar(x=x, y=equity_df[col], name=label, marker_color=color))

    year_list = equity_df['year'].tolist()
    for _, row in equity_df.iterrows():
        if row['total'] > 0:
            fig.add_annotation(
                x=year_list.index(row['year']), y=row['total'],
                text=f"{row['total']:,.0f}",
                showarrow=False, yshift=8, font=dict(size=10, color=_font_color()),
            )

    ytd_rows = equity_df[equity_df['year'] >= current_year]
    for _, row in ytd_rows.iterrows():
        fig.add_annotation(
            x=year_list.index(row['year']), y=row['total'],
            text="YTD", showarrow=False, yshift=22,
            font=dict(size=9, color='#999999'),
        )

    layout = _base_layout(
        title=f"Annual Equity {ref_label} Miles",
        xaxis_title="Year",
        yaxis_title=f"Equity {ref_label} Miles",
        barmode='stack',
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1, traceorder='normal'),
    )
    if height:
        layout['height'] = height
    fig.update_layout(**layout)
    fig.update_xaxes(type='category')
    fig.update_yaxes(gridcolor=_grid_color())
    return fig


def make_labeled_bar_chart(labels, values, title, x_label, y_label, color=None):
    """Generic bar chart with arbitrary string labels — useful for period-filtered views."""
    if color is None:
        color = STRAVA_ORANGE
    fig = go.Figure(go.Bar(
        x=list(labels),
        y=list(values),
        marker_color=color,
        text=[f"{v:,.0f}" for v in values],
        textposition='outside',
    ))
    fig.update_layout(**_base_layout(
        title=title,
        xaxis_title=x_label,
        yaxis_title=y_label,
        showlegend=False,
    ))
    fig.update_yaxes(gridcolor=_grid_color())
    return fig


def make_recent_months_chart(months_df, this_year, last_year, unit_label):
    """
    Grouped bar chart: this year (orange) vs last year (blue) for recent months.
    months_df columns: month_label, this_year_val, last_year_val, is_current.
    Current in-progress month is shown in the lighter orange tint.
    """
    this_year_colors = [
        STRAVA_ORANGE_LIGHT if row['is_current'] else STRAVA_ORANGE
        for _, row in months_df.iterrows()
    ]

    fig = go.Figure()

    fig.add_trace(go.Bar(
        x=months_df['month_label'],
        y=months_df['last_year_val'],
        name=str(last_year),
        marker_color=PRIOR_BLUE,
    ))

    fig.add_trace(go.Bar(
        x=months_df['month_label'],
        y=months_df['this_year_val'],
        name=str(this_year),
        marker_color=this_year_colors,
    ))

    fig.update_layout(**_base_layout(
        title=f"Monthly {unit_label} — {this_year} vs {last_year}",
        xaxis_title="Month",
        yaxis_title=unit_label,
        barmode='group',
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1, traceorder='normal'),
    ))
    fig.update_yaxes(gridcolor=_grid_color())
    return fig


def make_equity_monthly_chart(monthly_df, ref_label='Bike', goal=None):
    """
    Stacked bar chart of equity miles per month for a single year.
    Only adds traces for sports that have at least one non-zero value.
    Optional dashed monthly goal line.
    """
    fig = go.Figure()

    for col, color, label in _SPORT_COLORS:
        if col in monthly_df.columns and monthly_df[col].sum() > 0:
            fig.add_trace(go.Bar(
                x=monthly_df['month_name'], y=monthly_df[col],
                name=label, marker_color=color,
            ))

    if goal and goal > 0:
        fig.add_hline(
            y=goal,
            line_dash='dash', line_color='gray', line_width=1.5,
            annotation_text=f"Goal: {goal:,.0f}",
            annotation_position='top right', annotation_font_size=11,
        )

    fig.update_layout(**_base_layout(
        title=f"Monthly Equity {ref_label} Miles",
        xaxis_title="Month",
        yaxis_title=f"Equity {ref_label} Miles",
        barmode='stack',
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1, traceorder='normal'),
    ))
    fig.update_yaxes(gridcolor=_grid_color())
    return fig


def make_bike_heatmap(routes: list, center_lat: float, center_lon: float,
                      height: int = 560) -> go.Figure:
    """Geographic route heatmap.

    routes — list of [(lat, lon), ...] coordinate lists, one per ride.
    All routes are combined into a single Scattermapbox trace using None
    separators so Plotly draws each as a separate line efficiently.
    Map style is carto-darkmatter (dark mode) or open-street-map (light).
    No Mapbox API key required.
    """
    lats: list = []
    lons: list = []
    for route in routes:
        for la, lo in route:
            lats.append(la)
            lons.append(lo)
        lats.append(None)
        lons.append(None)

    fig = go.Figure(go.Scattermapbox(
        lat=lats,
        lon=lons,
        mode='lines',
        line=dict(width=1.5, color=STRAVA_ORANGE),
        opacity=0.45,
        hoverinfo='none',
    ))

    map_style = 'carto-darkmatter' if _dark else 'open-street-map'
    fig.update_layout(
        mapbox=dict(
            style=map_style,
            center=dict(lat=center_lat, lon=center_lon),
            zoom=10,
        ),
        margin=dict(t=0, b=0, l=0, r=0),
        height=height,
        paper_bgcolor=_paper_bg(),
    )
    return fig


def make_calendar_heatmap(daily_df, value_label='Miles', height=200):
    """GitHub-contribution-graph-style calendar heatmap: one column per week,
    one row per weekday, cell shade = that day's activity level.

    Levels are quantized into 3 relative tiers (terciles) among the *active*
    days in daily_df, plus a 4th neutral tier for no-activity days — relative
    binning so the graph is legible whether the period was a light month or a
    peak training block, rather than fixed absolute thresholds.
    daily_df — from process_data.build_daily_totals: one row per day, columns
    date/weekday/week_idx/miles/count.
    """
    if daily_df.empty:
        return go.Figure()

    active = daily_df.loc[daily_df['miles'] > 0, 'miles']
    q1, q2 = active.quantile([1 / 3, 2 / 3]) if not active.empty else (0, 0)

    def _level(v):
        if v <= 0:
            return 0
        if v <= q1:
            return 1
        if v <= q2:
            return 2
        return 3

    n_weeks = int(daily_df['week_idx'].max()) + 1
    z = [[0] * n_weeks for _ in range(7)]
    hover = [[''] * n_weeks for _ in range(7)]
    for row in daily_df.itertuples():
        wd, wk = row.weekday, row.week_idx
        z[wd][wk] = _level(row.miles)
        day_str = row.date.strftime('%a %b ') + str(row.date.day)
        hover[wd][wk] = (
            f"{day_str}<br>{row.miles:,.1f} {value_label.lower()} · {row.count} activities"
            if row.miles > 0 else f"{day_str}<br>No activity"
        )

    colors = CAL_HEATMAP_DARK if _dark else CAL_HEATMAP_LIGHT
    colorscale = [
        [0.00, colors[0]], [0.25, colors[0]],
        [0.25, colors[1]], [0.50, colors[1]],
        [0.50, colors[2]], [0.75, colors[2]],
        [0.75, colors[3]], [1.00, colors[3]],
    ]

    # Month tick labels at each month's first appearance in the grid. Skip a
    # label that would sit fewer than 2 week-columns after the previous one
    # (e.g. a period starting mid-month) — abbreviations collide at that
    # spacing, and the following month's label is only a couple weeks away.
    month_ticks, month_labels, seen = [], [], set()
    for row in daily_df.sort_values(['week_idx', 'weekday']).itertuples():
        key = (row.date.year, row.date.month)
        if key not in seen:
            seen.add(key)
            if month_ticks and row.week_idx - month_ticks[-1] < 2:
                continue
            month_ticks.append(row.week_idx)
            month_labels.append(row.date.strftime('%b'))

    fig = go.Figure(go.Heatmap(
        z=z,
        x=list(range(n_weeks)),
        y=['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
        customdata=hover,
        hovertemplate='%{customdata}<extra></extra>',
        colorscale=colorscale,
        zmin=0, zmax=3,
        xgap=3, ygap=3,
        showscale=False,
    ))
    fig.update_xaxes(
        tickmode='array', tickvals=month_ticks, ticktext=month_labels,
        side='top', showgrid=False, zeroline=False, tickangle=0,
    )
    fig.update_yaxes(showgrid=False, zeroline=False, autorange='reversed')
    fig.update_layout(**_base_layout(
        height=height,
        margin=dict(t=30, b=10, l=40, r=10),
    ))
    return fig
