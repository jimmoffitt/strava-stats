## What is this thing?

Not sure, but I know my Strava 2025 wrapped did not provide many data views I was interested in. I also have strong opinions that "wrapped" packages should not be generated until January 1. Or at least let us paying customers generate a customized one when we want.

A personal Strava activity dashboard — fetches data from the Strava API, archives it locally, and serves an interactive multi-sport analysis via Streamlit.

## Project structure

```
strava-stats/
├── data/
│   ├── raw/                 # Activity archive (JSON, not committed)
│   ├── processed/           # Intermediate outputs
│   ├── images/              # Static PNGs from pipeline
│   ├── gear_map.json        # Bike/shoe names (written by pipeline)
│   └── settings.json        # User goals and equity conversions
│
├── src/
│   ├── config.py            # Env vars, paths, sport constants, defaults
│   ├── fetch_data.py        # Strava API auth, token refresh, archive sync
│   ├── process_data.py      # Pandas processing and aggregation helpers
│   ├── charts.py            # Plotly figure factories (interactive dashboard)
│   └── publish_data.py      # Matplotlib figure factories (static pipeline)
│
├── app.py                   # Streamlit interactive dashboard
├── run_pipeline.py          # CLI pipeline: fetch → process → publish static PNGs
└── requirements.txt
```

## Quick start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Add credentials to .local.env
echo "STRAVA_CLIENT_ID=your_id" >> .local.env
echo "STRAVA_CLIENT_SECRET=your_secret" >> .local.env

# 3. Sync latest activities and generate gear map
python run_pipeline.py

# 4. Launch dashboard
streamlit run app.py
```

## Dashboard

The interactive dashboard is organized into tabs: Bike, Ski, Swim, Mile Equity, Wrapped, and Settings.

### Bike — Year view
Annual distance and riding hours, 2018–present. Current year shown in lighter orange (YTD).

![Bike year view](docs/screenshots/bike_year.png)

### Bike — Month view
Day-by-day comparison: selected month (orange) vs. prior year same month (blue) vs. current month in progress (gray).

![Bike month view](docs/screenshots/bike_month.png)

### Bike — Week view
ISO week navigator with the same three-period overlay.

![Bike week view](docs/screenshots/bike_week.png)

### Settings
Equity mile conversion rates (swim meters and ski vertical feet per mile) and annual/monthly/seasonal goals.

![Settings tab](docs/screenshots/settings.png)

## Equity miles

Normalizes cross-sport effort to a common "bike mile" unit:

| Sport | Default conversion |
|---|---|
| Bike | 1 mile = 1 equity mile (reference) |
| Swim | 100 meters = 1 equity mile |
| Ski  | 1,000 vertical feet = 1 equity mile |

Conversion rates and goals are configurable in the Settings tab and persisted to `data/settings.json`.

## Data storage and persistence

All data lives in the `data/` directory and persists between sessions. Nothing is sent to a remote server — everything stays local.

### Activity archive

The primary data store is `data/raw/my_strava_activities.json`, a flat JSON array of every activity ever synced. It grows incrementally:

- **Past years** — fetched once and never re-fetched. If 2023 is already in the archive, the pipeline skips it entirely.
- **Current year** — checked on every pipeline run. Only activities newer than the most recent archived timestamp are fetched and appended.

The archive is not committed to git (listed in `.gitignore`). The dashboard reads it directly on startup via `app.py`'s cached `load_activities()`.

### Supporting JSON files

| File | Written by | Contains |
|---|---|---|
| `data/raw/my_strava_activities.json` | `run_pipeline.py` | Full activity archive |
| `data/gear_map.json` | `run_pipeline.py` | Bike/shoe ID → name mapping |
| `data/athlete_profile.json` | `run_pipeline.py` | Name, location, follower count |
| `data/athlete_stats.json` | `run_pipeline.py` | All-time and YTD Strava totals |
| `data/settings.json` | Settings tab in app | Goals and equity conversion rates |

`settings.json` is written by the Streamlit app when you save changes in the Settings tab, not by the pipeline. All other files require running `python run_pipeline.py`.

### How the dashboard loads data

On startup, `app.py` calls `load_activities()` (decorated with `@st.cache_data`):

1. Reads `data/raw/my_strava_activities.json` as the base archive.
2. Scans `data/raw/` for any per-year files (e.g. `2026.json`) whose year is **not** already in the archive, and merges them in. This handles partially-synced years before the pipeline runs.
3. Processes the merged list into a pandas DataFrame via `process_data.process_activities()`.

The result is cached for the lifetime of the Streamlit session, so navigating between tabs does not re-read or re-process the files.

### Keeping data current

Run the pipeline whenever you want to pull in new activities:

```bash
python run_pipeline.py
```

Then reload the dashboard (`r` in the browser or restart `streamlit run app.py`) to pick up the updated archive.

## Strava activity data

Key fields used from the Strava API:

```json
{
    "name": "Lily dropped me off wander",
    "distance": 26215.8,
    "moving_time": 5587,
    "total_elevation_gain": 141.9,
    "type": "Ride",
    "sport_type": "Ride",
    "start_date": "2025-12-31T17:29:50Z",
    "start_date_local": "2025-12-31T10:29:50Z",
    "timezone": "(GMT-07:00) America/Denver",
    "gear_id": "b9657721",
    "average_watts": 110.9,
    "kilojoules": 619.5
}
```
