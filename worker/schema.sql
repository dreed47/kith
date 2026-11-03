-- One row per Kith site that has asked for the newest version: its random ID, the version it last said it runs,
-- and the first and last days it asked. No addresses or anything else about the site.
CREATE TABLE IF NOT EXISTS sites (
  site TEXT PRIMARY KEY,
  version TEXT NOT NULL,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sites_last_seen ON sites (last_seen);
