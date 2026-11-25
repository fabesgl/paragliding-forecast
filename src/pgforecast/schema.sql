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
CREATE TABLE IF NOT EXISTS forecast (        -- Phase 3
  site_id TEXT, point_key TEXT, model TEXT, lead_days INTEGER,
  valid_time_utc TEXT, variable TEXT, value REAL,
  PRIMARY KEY (site_id, point_key, model, lead_days, valid_time_utc, variable));
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
