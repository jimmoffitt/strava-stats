# src/charts.py — Plotly figure factories for the Streamlit dashboard
# Pure functions: no Streamlit calls, each returns go.Figure

import plotly.graph_objects as go

STRAVA_ORANGE = '#FC4C02'
STRAVA_ORANGE_LIGHT = '#FCAB87'
PRIOR_BLUE = 'rgba(70, 130, 200, 0.5)'
SHADOW_GRAY = 'rgba(150, 150, 150, 0.25)'


def make_year_dist_chart(yearly_df, dist_col, dist_label, current_year):
    """
    Bar chart of distance by year.
    Current year bar uses light tint (YTD); prior years use Strava orange.
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

    # Annotate the current-year bar with "YTD"
    ytd_rows = yearly_df[yearly_df['year'] >= current_year]
    for _, row in ytd_rows.iterrows():
        fig.add_annotation(
            x=str(int(row['year'])),
            y=row[dist_col],
            text="YTD",
            showarrow=False,
            yshift=28,
            font=dict(size=10, color=STRAVA_ORANGE_LIGHT),
        )

    fig.update_layout(
        title=f"Annual Distance ({dist_label})",
        xaxis_title="Year",
        yaxis_title=dist_label,
        plot_bgcolor='white',
        margin=dict(t=50, b=40, l=40, r=20),
        showlegend=False,
    )
    fig.update_yaxes(gridcolor='#eeeeee')
    return fig


def make_year_time_chart(yearly_df, current_year):
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

    ytd_rows = yearly_df[yearly_df['year'] >= current_year]
    for _, row in ytd_rows.iterrows():
        fig.add_annotation(
            x=str(int(row['year'])),
            y=row['hours'],
            text="YTD",
            showarrow=False,
            yshift=28,
            font=dict(size=10, color=STRAVA_ORANGE_LIGHT),
        )

    fig.update_layout(
        title="Annual Riding Hours",
        xaxis_title="Year",
        yaxis_title="Hours",
        plot_bgcolor='white',
        margin=dict(t=50, b=40, l=40, r=20),
        showlegend=False,
    )
    fig.update_yaxes(gridcolor='#eeeeee')
    return fig


def make_period_comparison_chart(
    ref_df, prior_df, shadow_df,
    x_col, x_label, dist_col, dist_label, title,
):
    """
    Overlay bar chart comparing up to three periods.
    - ref_df   : reference period (solid Strava orange)
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
            marker_color=STRAVA_ORANGE,
        ))

    fig.update_layout(
        title=title,
        xaxis_title=x_label,
        yaxis_title=dist_label,
        barmode='overlay',
        plot_bgcolor='white',
        margin=dict(t=50, b=40, l=40, r=20),
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
    )
    fig.update_yaxes(gridcolor='#eeeeee')
    return fig


def make_monthly_chart(monthly_df, dist_col, dist_label, goal=None):
    """Bar chart of distance by month — 12 bars, 0-filled for empty months.
    Optional dashed goal line."""
    fig = go.Figure(go.Bar(
        x=monthly_df['month_name'],
        y=monthly_df[dist_col],
        marker_color=STRAVA_ORANGE,
        text=[f"{v:,.0f}" if v > 0 else "" for v in monthly_df[dist_col]],
        textposition='outside',
    ))
    if goal and goal > 0:
        fig.add_hline(
            y=goal,
            line_dash='dash',
            line_color='gray',
            line_width=1.5,
            annotation_text=f"Goal: {goal:,.0f}",
            annotation_position='top right',
            annotation_font_size=11,
        )
    fig.update_layout(
        title=f"Distance by Month ({dist_label})",
        xaxis_title="Month",
        yaxis_title=dist_label,
        plot_bgcolor='white',
        margin=dict(t=50, b=40, l=40, r=20),
        showlegend=False,
    )
    fig.update_yaxes(gridcolor='#eeeeee')
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
    fig.update_layout(
        title=f"Distance by Sport ({dist_label})",
        xaxis_title=dist_label,
        plot_bgcolor='white',
        margin=dict(t=50, b=40, l=60, r=60),
        showlegend=False,
    )
    fig.update_xaxes(gridcolor='#eeeeee')
    return fig


SWIM_TEAL = '#00B4D8'
SWIM_TEAL_LIGHT = '#90E0EF'


def make_swim_year_chart(yearly_df, current_year, annual_goal=None):
    """
    Bar chart of total meters (or yards) per year.
    Current year lighter; optional dashed annual-goal line.
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

    ytd_rows = yearly_df[yearly_df['year'] >= current_year]
    for _, row in ytd_rows.iterrows():
        fig.add_annotation(
            x=str(int(row['year'])), y=row[y_col],
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

    fig.update_layout(
        title="Annual Distance",
        xaxis_title="Year",
        yaxis_title=y_col.capitalize(),
        plot_bgcolor='white',
        margin=dict(t=50, b=40, l=40, r=20),
        showlegend=False,
    )
    fig.update_yaxes(gridcolor='#eeeeee')
    return fig


SKI_BLUE = '#4A90D9'
SKI_BLUE_LIGHT = '#A8CBF0'


def make_season_vert_chart(seasonal_df, current_season_key, goal_vert=None):
    """
    Bar chart of total vertical feet by ski season.
    Current/in-progress season shown in lighter blue; past seasons in solid blue.
    Optional dashed goal line.
    """
    colors = [
        SKI_BLUE_LIGHT if int(row['season_key']) >= current_season_key else SKI_BLUE
        for _, row in seasonal_df.iterrows()
    ]

    fig = go.Figure(go.Bar(
        x=seasonal_df['season_label'],
        y=seasonal_df['vert_ft'],
        marker_color=colors,
        text=[f"{v:,.0f}" for v in seasonal_df['vert_ft']],
        textposition='outside',
    ))

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

    fig.update_layout(
        title="Season Vertical Feet",
        xaxis_title="Season",
        yaxis_title="Vertical Feet",
        plot_bgcolor='white',
        margin=dict(t=50, b=40, l=40, r=20),
        showlegend=False,
    )
    fig.update_yaxes(gridcolor='#eeeeee')
    return fig


def make_equity_annual_chart(equity_df, current_year):
    """
    Stacked bar chart of equity miles per year broken down by bike / ski / swim.
    Total label annotated above each bar; current year marked YTD.
    """
    fig = go.Figure()

    fig.add_trace(go.Bar(
        x=equity_df['year'].astype(str),
        y=equity_df['bike'],
        name='Bike',
        marker_color=STRAVA_ORANGE,
    ))
    fig.add_trace(go.Bar(
        x=equity_df['year'].astype(str),
        y=equity_df['ski'],
        name='Ski',
        marker_color=SKI_BLUE,
    ))
    fig.add_trace(go.Bar(
        x=equity_df['year'].astype(str),
        y=equity_df['swim'],
        name='Swim',
        marker_color=SWIM_TEAL,
    ))

    for _, row in equity_df.iterrows():
        if row['total'] > 0:
            fig.add_annotation(
                x=str(int(row['year'])), y=row['total'],
                text=f"{row['total']:,.0f}",
                showarrow=False, yshift=8, font=dict(size=10),
            )

    ytd_rows = equity_df[equity_df['year'] >= current_year]
    for _, row in ytd_rows.iterrows():
        fig.add_annotation(
            x=str(int(row['year'])), y=row['total'],
            text="YTD", showarrow=False, yshift=22,
            font=dict(size=9, color='#999999'),
        )

    fig.update_layout(
        title="Annual Equity Miles",
        xaxis_title="Year",
        yaxis_title="Equity Miles",
        barmode='stack',
        plot_bgcolor='white',
        margin=dict(t=50, b=40, l=40, r=20),
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
    )
    fig.update_yaxes(gridcolor='#eeeeee')
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
    fig.update_layout(
        title=title,
        xaxis_title=x_label,
        yaxis_title=y_label,
        plot_bgcolor='white',
        margin=dict(t=50, b=40, l=40, r=20),
        showlegend=False,
    )
    fig.update_yaxes(gridcolor='#eeeeee')
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

    fig.update_layout(
        title=f"Monthly {unit_label} — {this_year} vs {last_year}",
        xaxis_title="Month",
        yaxis_title=unit_label,
        barmode='group',
        plot_bgcolor='white',
        margin=dict(t=50, b=40, l=40, r=20),
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
    )
    fig.update_yaxes(gridcolor='#eeeeee')
    return fig


def make_equity_monthly_chart(monthly_df, goal=None):
    """
    Stacked bar chart of equity miles per month for a single year.
    Optional dashed monthly goal line.
    """
    fig = go.Figure()

    fig.add_trace(go.Bar(
        x=monthly_df['month_name'],
        y=monthly_df['bike'],
        name='Bike',
        marker_color=STRAVA_ORANGE,
    ))
    fig.add_trace(go.Bar(
        x=monthly_df['month_name'],
        y=monthly_df['ski'],
        name='Ski',
        marker_color=SKI_BLUE,
    ))
    fig.add_trace(go.Bar(
        x=monthly_df['month_name'],
        y=monthly_df['swim'],
        name='Swim',
        marker_color=SWIM_TEAL,
    ))

    if goal and goal > 0:
        fig.add_hline(
            y=goal,
            line_dash='dash', line_color='gray', line_width=1.5,
            annotation_text=f"Goal: {goal:,.0f}",
            annotation_position='top right', annotation_font_size=11,
        )

    fig.update_layout(
        title="Monthly Equity Miles",
        xaxis_title="Month",
        yaxis_title="Equity Miles",
        barmode='stack',
        plot_bgcolor='white',
        margin=dict(t=50, b=40, l=40, r=20),
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
    )
    fig.update_yaxes(gridcolor='#eeeeee')
    return fig
