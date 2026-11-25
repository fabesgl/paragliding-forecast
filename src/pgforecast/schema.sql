CREATE TABLE IF NOT EXISTS site (
  id TEXT PRIMARY KEY, name TEXT, timezone TEXT);
CREATE TABLE IF NOT EXISTS point (
  site_id TEXT REFERENCES site(id), key TEXT, lat REAL, lon REAL,
  elevation_m REAL, role TEXT, status TEXT, PRIMARY KEY (site_id, key));
CREATE TABLE IF NOT EXISTS flight (          -- Phase 2; stays local, never pushed
  id TEXT PRIMARY KEY, site_id TEXT, date TEXT, launch_time_local TEXT,
  pilot_hash TEXT, launch_name TEXT, launch_lat REAL, launch_lon REAL,
  distance_km REAL, points REAL, route_type TEXT, glider_model TEXT,
  glider_class TEXT, duration_min REAL);
CREATE TABLE IF NOT EXISTS fc (              -- Phase 3: one row per hour per model
  source TEXT,            -- 'hist' = Historical Forecast API (short lead), 'prev' = Previous Runs API
  lead_days INTEGER,      -- 0 for hist; 1 or 2 for prev
  point_key TEXT, model TEXT, time_utc TEXT,
  wind_speed_10m REAL, wind_direction_10m REAL, wind_gusts_10m REAL,
  wind_speed_850hPa REAL, wind_direction_850hPa REAL, wind_speed_700hPa REAL, wind_direction_700hPa REAL,
  temperature_2m REAL, dew_point_2m REAL, temperature_850hPa REAL, temperature_700hPa REAL,
  cloud_cover_low REAL, cloud_cover_mid REAL, cloud_cover_high REAL,
  cape REAL, lifted_index REAL, convective_inhibition REAL, boundary_layer_height REAL,
  shortwave_radiation REAL, precipitation REAL, pressure_msl REAL,
  PRIMARY KEY (source, lead_days, point_key, model, time_utc));
CREATE TABLE IF NOT EXISTS fetch_log (       -- makes downloads resumable
  source TEXT, lead_days INTEGER, point_key TEXT, model TEXT, start_date TEXT, end_date TEXT,
  fetched_at_utc TEXT, n_rows INTEGER, PRIMARY KEY (source, lead_days, point_key, model, start_date));
CREATE TABLE IF NOT EXISTS bom_obs (         -- step 2
  station_id TEXT, obs_time_utc TEXT, air_temp REAL, dew_point REAL,
  wind_dir TEXT, wind_dir_deg REAL, wind_kmh REAL, gust_kmh REAL,
  pressure_hpa REAL, rain_mm REAL, rel_hum REAL,
  PRIMARY KEY (station_id, obs_time_utc));
CREATE TABLE IF NOT EXISTS day_label (       -- Phase 5
  site_id TEXT, date TEXT, flyable INTEGER, xc_score REAL, acro_score REAL,
  weight REAL, PRIMARY KEY (site_id, date));
CREATE TABLE IF NOT EXISTS prediction_log (
  site_id TEXT, made_at_utc TEXT, target_date TEXT, model TEXT,
  xc_pct REAL, acro_pct REAL, reasons TEXT,
  PRIMARY KEY (site_id, made_at_utc, target_date, model));
