-- Kith's own names: TNG's tables lose their tng_ prefix, and the ones Kith never uses (albums, DNA, TNG's
-- page templates and settings) are dropped. Repositories and branches stay: they hold tree data.
-- Safe to run again: IF EXISTS everywhere, and the audit log update only touches old names.
RENAME TABLE IF EXISTS tng_addresses TO addresses;
RENAME TABLE IF EXISTS tng_branches TO branches;
RENAME TABLE IF EXISTS tng_branchlinks TO branchlinks;
RENAME TABLE IF EXISTS tng_cemeteries TO cemeteries;
RENAME TABLE IF EXISTS tng_children TO children;
RENAME TABLE IF EXISTS tng_citations TO citations;
RENAME TABLE IF EXISTS tng_events TO events;
RENAME TABLE IF EXISTS tng_eventtypes TO eventtypes;
RENAME TABLE IF EXISTS tng_families TO families;
RENAME TABLE IF EXISTS tng_media TO media;
RENAME TABLE IF EXISTS tng_medialinks TO medialinks;
RENAME TABLE IF EXISTS tng_mediatypes TO mediatypes;
RENAME TABLE IF EXISTS tng_notelinks TO notelinks;
RENAME TABLE IF EXISTS tng_people TO people;
RENAME TABLE IF EXISTS tng_places TO places;
RENAME TABLE IF EXISTS tng_repositories TO repositories;
RENAME TABLE IF EXISTS tng_sources TO sources;
RENAME TABLE IF EXISTS tng_timelineevents TO timelineevents;
RENAME TABLE IF EXISTS tng_users TO users;
RENAME TABLE IF EXISTS tng_xnotes TO xnotes;
DROP TABLE IF EXISTS tng_albumlinks, tng_albumplinks, tng_albums, tng_associations, tng_countries, tng_dna_groups, tng_dna_links, tng_dna_tests, tng_image_tags, tng_languages, tng_mostwanted, tng_reports, tng_saveimport, tng_states, tng_temp_events, tng_templates, tng_trees;
-- The edit history names the table each change was made in; What's new, Activity and hand-ordered children read it.
UPDATE app_audit SET tbl = SUBSTRING(tbl, 5) WHERE tbl LIKE 'tng\_%';
