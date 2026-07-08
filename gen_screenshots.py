"""
Generate README screenshots from real activity data.
Run once: python gen_screenshots.py
"""
import json
import os
import sys
from datetime import date

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

from src import config, process_data
from src.charts import (
    make_equity_annual_chart,
    make_season_vert_chart,
    make_swim_year_chart,
    make_year_dist_chart,
    make_year_time_chart,
)
from src.config import BIKE_TYPES, SKI_TYPES, SWIM_TYPES

OUT_DIR = 'docs/screenshots'
os.makedirs(OUT_DIR, exist_ok=True)

# --- Load data ---
with open(config.ACTIVITIES_FILE) as f:
    all_activities = json.load(f)

# Merge per-year files for years not in archive
present_years = {int(a.get('start_date', a.get('start_date_local', ''))[:4])
                 for a in all_activities if a.get('start_date') or a.get('start_date_local')}
for fname in os.listdir(config.RAW_DIR):
    stem = fname[:-5]
    if fname.endswith('.json') and stem.isdigit() and int(stem) not in present_years:
        with open(os.path.join(config.RAW_DIR, fname)) as f:
            extra = json.load(f)
        if isinstance(extra, list):
            all_activities.extend(extra)

df = process_data.process_activities(all_activities)
print(f"Loaded {len(df)} activities, years {sorted(df['year'].unique().tolist())}")

_eq_mask = df['name'].str.match(process_data._EQ_PATTERN, na=False)
bike_df  = df[df['final_type'].isin(BIKE_TYPES)].copy()
ski_df   = df[df['final_type'].isin(SKI_TYPES) & ~_eq_mask].copy()
swim_df  = df[df['final_type'].isin(SWIM_TYPES) & ~_eq_mask].copy()

today        = date.today()
current_year = today.year

with open(config.SETTINGS_FILE) as f:
    settings = json.load(f)

# --- Combined / equity annual (thin) ---
equity_yearly = process_data.aggregate_equity_by_year(df, settings)
fig = make_equity_annual_chart(equity_yearly, current_year, height=300)
fig.write_image(os.path.join(OUT_DIR, 'combined_annual.png'), width=1200, height=300)
print("wrote combined_annual.png")

# --- Bike annual (thin) ---
bike_yearly = process_data.aggregate_by_year(bike_df)
fig = make_year_dist_chart(bike_yearly, 'miles', 'Miles', current_year, height=300)
fig.write_image(os.path.join(OUT_DIR, 'bike_annual.png'), width=1200, height=300)
print("wrote bike_annual.png")

# --- Bike hours ---
fig = make_year_time_chart(bike_yearly, current_year)
fig.write_image(os.path.join(OUT_DIR, 'bike_hours.png'), width=900, height=500)
print("wrote bike_hours.png")

# --- Snow / ski annual (thin) ---
ski_seasonal = process_data.aggregate_ski_by_season(ski_df)
current_season_key = today.year if today.month >= 10 else today.year - 1
fig = make_season_vert_chart(ski_seasonal, current_season_key, height=300)
fig.write_image(os.path.join(OUT_DIR, 'snow_annual.png'), width=1200, height=300)
print("wrote snow_annual.png")

# --- Snow season detail (most recent full season) ---
past_seasons = ski_seasonal[ski_seasonal['season_key'] < current_season_key]
if not past_seasons.empty:
    last_key = past_seasons['season_key'].max()
else:
    last_key = ski_seasonal['season_key'].max()
goal_vert = settings.get('goals', {}).get('ski_season_vert_ft', 200000)
fig = make_season_vert_chart(ski_seasonal, current_season_key, goal_vert=goal_vert)
fig.write_image(os.path.join(OUT_DIR, 'snow_season_chart.png'), width=900, height=500)
print("wrote snow_season_chart.png")

# --- Swim annual (thin) ---
swim_yearly = process_data.aggregate_swim_by_year(swim_df)
fig = make_swim_year_chart(swim_yearly, current_year, height=300)
fig.write_image(os.path.join(OUT_DIR, 'swim_annual.png'), width=1200, height=300)
print("wrote swim_annual.png")

print("Done.")
