"""
make_demo_data.py — Build the sanitized demo dataset for read-only deploys.

Reads the real (gitignored) activity archive and writes data/demo/ copies
that are safe to commit to the public repo:

  data/demo/activities.json — the archive with each activity reduced to the
      whitelisted fields below. Everything else is dropped, notably GPS
      (start_latlng / end_latlng / map polylines), heartrate, power, device
      names, and location strings.
  data/demo/gear_map.json — copy of the gear-id → display-name map so the
      bike tables show bike names.

The app switches to this dataset in demo mode (see DEMO_MODE in src/config.py).
Rerun this script after a sync to refresh the demo data, then review and
commit the result.
"""
import json
import os

from src import config

# Every field app.py / src/process_data.py actually reads, and nothing more.
# (name is kept so the Recent/Longest tables read naturally — review the
# printed sample before committing if any activity names are personal.)
FIELD_WHITELIST = [
    'id',
    'name',
    'type',
    'sport_type',
    'start_date',
    'start_date_local',
    'distance',
    'moving_time',
    'elapsed_time',
    'total_elevation_gain',
    'gear_id',
    'average_speed',
    'kudos_count',
    'athlete_count',
]

# Real paths, deliberately independent of config's DEMO_MODE redirection so
# this script always reads the true archive even if STRAVA_STATS_DEMO is set.
REAL_ARCHIVE  = os.path.join('data', 'raw', 'my_strava_activities.json')
REAL_GEAR_MAP = os.path.join('data', 'gear_map.json')
DEMO_DIR      = os.path.join('data', 'demo')


def main():
    with open(REAL_ARCHIVE) as f:
        activities = json.load(f)

    sanitized = [
        {k: act[k] for k in FIELD_WHITELIST if k in act}
        for act in activities
    ]

    os.makedirs(DEMO_DIR, exist_ok=True)
    out_path = os.path.join(DEMO_DIR, 'activities.json')
    with open(out_path, 'w') as f:
        json.dump(sanitized, f)

    gear_map = dict(config.GEAR_FALLBACKS)
    if os.path.exists(REAL_GEAR_MAP):
        with open(REAL_GEAR_MAP) as f:
            gear_map.update(json.load(f))
    with open(os.path.join(DEMO_DIR, 'gear_map.json'), 'w') as f:
        json.dump(gear_map, f, indent=2)

    # Report what was kept and dropped so the result is easy to eyeball.
    dropped = sorted({k for act in activities for k in act} - set(FIELD_WHITELIST))
    names = sorted({act.get('name', '') for act in sanitized})
    print(f"Wrote {len(sanitized):,} activities -> {out_path}")
    print(f"Kept fields:    {', '.join(FIELD_WHITELIST)}")
    print(f"Dropped fields: {', '.join(dropped)}")
    print(f"Distinct activity names ({len(names)}):")
    for n in names:
        print(f"  {n}")


if __name__ == '__main__':
    main()
