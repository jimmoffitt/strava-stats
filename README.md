# Strava Stats

A personal Strava dashboard that goes deeper than the official app. Built because the annual "Wrapped" summary doesn't answer the questions I actually care about: How does this year compare to last? Am I riding more or less than 2021? How do ski and swim effort stack up against bike miles?

Eight interactive tabs cover Bike, Snow, Swim, cross-sport equity, a Wrapped-style summary, full-text activity search, data export, and goals. Data syncs directly from the Strava API and is stored locally — nothing leaves your machine.

![Combined annual chart](docs/screenshots/combined_annual.png)

---

## What it does

### Multi-sport dashboard

Each sport tab opens with a thin, full-width overview chart showing every year at a glance, then drops into per-year or per-season detail with a compact stats bar.

**Bike** — annual miles and hours, month/week comparison charts (selected period vs. prior year vs. current in-progress), and a gear filter to isolate rides by bike.

![Bike annual miles](docs/screenshots/bike_annual.png)
![Bike annual hours](docs/screenshots/bike_hours.png)

**Snow** — vertical feet by season, days on snow, biggest days, and a full season log. Stats bar shows max single day, avg vert/day, and equity miles.

![Snow overview](docs/screenshots/snow_annual.png)
![Snow season detail](docs/screenshots/snow_season_chart.png)

**Swim** — annual meters (or yards), monthly breakdown, goal progress bar, and a swim log. Meters/Yards toggle applies throughout.

![Swim annual](docs/screenshots/swim_annual.png)

### Equity miles

Different sports aren't directly comparable by distance, so this dashboard normalizes everything to a common "bike mile" unit:

| Sport | Default conversion |
|---|---|
| Bike | 1 mile = 1 equity mile (reference) |
| Swim | 100 meters = 1 equity mile |
| Ski  | 1,000 vertical feet = 1 equity mile |

The **Combined** tab stacks equity miles by sport for each year so you can see total fitness output regardless of which sports you focused on. Conversion rates are configurable in the Settings tab.

Activities with equity markers in their name (`SEq`, `HEq`, `GEq`, etc.) are manual equity declarations — they're listed separately and excluded from calculated totals to avoid double-counting.

### Live data sync

The sidebar shows the last sync age and total activity count. Click **Sync Now** to pull new activities from Strava without leaving the browser — it runs an incremental fetch, clears the data cache, and reloads automatically.

Past years are fetched once and archived. Only the current year is re-checked on each sync.

### Other tabs

**Wrapped** — pick any rolling window (last 365 days, last 30 days, a specific year or month) and a sport filter to get a period-in-review summary with charts and sport breakdown.

**Explore** — full-text search across all activities with date-range and sport-type filters. Results table with CSV download.

**Export** — annual summaries, monthly breakdowns, and a full activity table, each with PNG download and a combined ZIP.

**Settings** — set equity mile conversion rates and annual/monthly/seasonal goals. Changes are written to `data/settings.json` immediately.

---

## Quick start

```bash
# 1. Clone and install
git clone https://github.com/jimmoffitt/strava-stats.git
cd strava-stats
pip install -r requirements.txt

# 2. Add your Strava credentials
echo "STRAVA_CLIENT_ID=your_id" >> .local.env
echo "STRAVA_CLIENT_SECRET=your_secret" >> .local.env

# 3. Complete the Strava OAuth flow once to get a token
#    See: https://developers.strava.com/docs/getting-started/
#    The token file lives at data/strava_tokens.json

# 4. Fetch your activity history
python run_pipeline.py

# 5. Launch the dashboard
streamlit run app.py
```

After the first run, use the **Sync Now** button in the sidebar for incremental updates.

---

## How it's built

### Stack

| Package | Role |
|---|---|
| [Streamlit](https://streamlit.io) | Dashboard framework and UI |
| [Plotly](https://plotly.com/python/) | Interactive charts |
| [pandas](https://pandas.pydata.org) | Data processing and aggregation |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | Credentials from `.local.env` |
| [requests](https://docs.python-requests.org) | Strava API calls and token refresh |
| [kaleido](https://github.com/plotly/Kaleido) | Static PNG export (Export tab) |

### Project structure

```
strava-stats/
├── app.py                   # Streamlit dashboard — all tab render functions
├── run_pipeline.py          # CLI: fetch → process → publish (static PNGs)
│
├── src/
│   ├── config.py            # Env vars, file paths, sport type constants, defaults
│   ├── fetch_data.py        # Strava OAuth, token refresh, incremental archive sync
│   ├── process_data.py      # pandas aggregations: by year, season, month, week
│   ├── charts.py            # Plotly figure factories (one function per chart type)
│   └── publish_data.py      # Matplotlib figure factories (legacy static pipeline)
│
└── data/                    # All local data — not committed to git
    ├── raw/                 # my_strava_activities.json + per-year YYYY.json files
    ├── processed/           # Intermediate outputs from pipeline
    ├── images/              # Static PNGs from pipeline (legacy)
    ├── gear_map.json        # Bike ID → name mapping
    ├── last_data.json       # Last sync timestamp and count
    └── settings.json        # Goals and equity conversion rates
```

### Data flow

1. `fetch_data.py` pulls activities from the Strava API and appends them to `data/raw/my_strava_activities.json` (a flat JSON array).
2. `app.py` reads the archive on startup via a cached `load_activities()` call, auto-merging any per-year `data/raw/YYYY.json` files for years not already in the main archive.
3. `process_data.process_activities()` converts the raw list to a pandas DataFrame, adding derived columns (`distance_miles`, `elevation_feet`, `final_type`, `year`, `hours`).
4. Each tab's render function calls aggregation helpers (`aggregate_by_year`, `aggregate_ski_by_season`, `aggregate_equity_by_year`, etc.) and passes the results to Plotly figure factories in `charts.py`.

### Key data fields

```json
{
    "name": "Morning ride",
    "distance": 26215.8,
    "moving_time": 5587,
    "total_elevation_gain": 141.9,
    "type": "Ride",
    "sport_type": "Ride",
    "start_date_local": "2025-06-15T08:30:00Z",
    "gear_id": "b9657721"
}
```

`distance` is meters; `total_elevation_gain` is meters. The processing layer converts to miles and feet.

### Adding a new chart

1. Add a pure function to `src/charts.py` that accepts a DataFrame and returns a `go.Figure`.
2. Call the aggregation helper you need from `src/process_data.py` (or add one there).
3. Call `st.plotly_chart(your_fig, use_container_width=True)` inside the relevant `render_*` function in `app.py`.

---

## Configuration

### Credentials — `.local.env`

```
STRAVA_CLIENT_ID=your_client_id
STRAVA_CLIENT_SECRET=your_client_secret
STRAVA_YEARS=2024,2025          # optional: years to fetch on first run
```

### OAuth token — `data/strava_tokens.json`

Generated by completing the Strava OAuth flow once. The pipeline refreshes it automatically every 6 hours. Never commit this file.

```json
{
    "access_token": "...",
    "refresh_token": "...",
    "expires_at": 1234567890,
    "token_type": "Bearer"
}
```

### Goals and conversions — `data/settings.json`

Created automatically with defaults on first run. Edit via the Settings tab or directly:

```json
{
  "conversions": {
    "swim_meters_per_mile": 100,
    "ski_vert_per_mile": 1000
  },
  "goals": {
    "annual_equity_miles": 3000,
    "monthly_equity_miles": 250,
    "ski_season_vert_ft": 200000,
    "swim_monthly_meters": 10000
  }
}
```
