-- The site's own tables (app_), as the app used to create them when first needed, and the 2026-10-29 collation
-- fix for tables made before their DDL named TNG's collation. Safe on a site that already has them.

CREATE TABLE IF NOT EXISTS app_audit (
  id INT AUTO_INCREMENT PRIMARY KEY, at DATETIME NOT NULL, username VARCHAR(100) NOT NULL,
  action VARCHAR(40) NOT NULL, tbl VARCHAR(40) NOT NULL, record VARCHAR(60) COLLATE utf8mb3_general_ci NOT NULL,
  before_json MEDIUMTEXT, after_json MEDIUMTEXT, KEY record (tbl, record)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS app_citation_media (
  citationID INT NOT NULL, mediaID INT NOT NULL, ordernum INT NOT NULL,
  PRIMARY KEY (citationID, mediaID), KEY media (mediaID)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS app_comments (
  id INT AUTO_INCREMENT PRIMARY KEY, gedcom VARCHAR(20) COLLATE utf8mb3_general_ci NOT NULL, kind VARCHAR(10) NOT NULL,
  target VARCHAR(50) COLLATE utf8mb3_general_ci NOT NULL, created_at DATETIME NOT NULL, userID INT NOT NULL, username VARCHAR(100) NOT NULL,
  body TEXT NOT NULL, KEY target (gedcom, kind, target)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS app_face_tags (
  id INT AUTO_INCREMENT PRIMARY KEY, mediaID INT NOT NULL, gedcom VARCHAR(20) COLLATE utf8mb3_general_ci NOT NULL, personID VARCHAR(22) COLLATE utf8mb3_general_ci NOT NULL,
  x FLOAT NOT NULL, y FLOAT NOT NULL, w FLOAT NOT NULL, h FLOAT NOT NULL,
  created_by VARCHAR(100) NOT NULL, created_at DATETIME NOT NULL, KEY media (mediaID), KEY person (gedcom, personID)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS app_feedback (
  id INT AUTO_INCREMENT PRIMARY KEY, created_at DATETIME NOT NULL, userID INT NOT NULL, username VARCHAR(100) NOT NULL,
  kind VARCHAR(10) NOT NULL, message TEXT NOT NULL, page VARCHAR(500) NOT NULL DEFAULT '',
  context TEXT, screenshot VARCHAR(100) NOT NULL DEFAULT '', status VARCHAR(10) NOT NULL DEFAULT 'new',
  reply TEXT, github_issue INT, github_error VARCHAR(500), KEY status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS app_feedback_comments (
  id INT AUTO_INCREMENT PRIMARY KEY, feedback_id INT NOT NULL, created_at DATETIME NOT NULL, userID INT NOT NULL,
  username VARCHAR(100) NOT NULL, body TEXT NOT NULL, KEY feedback_id (feedback_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS app_geocode (
  gedcom VARCHAR(20) COLLATE utf8mb3_general_ci NOT NULL, place VARCHAR(248) COLLATE utf8mb3_general_ci NOT NULL, status VARCHAR(12) NOT NULL, matched VARCHAR(255) NOT NULL,
  at DATETIME NOT NULL, PRIMARY KEY (gedcom, place)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS app_imports (
  id INT AUTO_INCREMENT PRIMARY KEY, at DATETIME NOT NULL, username VARCHAR(100) NOT NULL,
  filename VARCHAR(255) NOT NULL, created MEDIUMTEXT NOT NULL, undone_at DATETIME
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS app_link_requests (
  id INT AUTO_INCREMENT PRIMARY KEY, userID INT NOT NULL, personID VARCHAR(22) COLLATE utf8mb3_general_ci NOT NULL,
  requested_at DATETIME NOT NULL, status VARCHAR(10) NOT NULL DEFAULT 'pending',
  decided_by VARCHAR(100), decided_at DATETIME, KEY pending (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS app_settings (
  name VARCHAR(40) PRIMARY KEY, value TEXT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS app_surname_aliases (
  gedcom VARCHAR(20) NOT NULL, name VARCHAR(127) NOT NULL, main VARCHAR(127) NOT NULL, PRIMARY KEY (gedcom, name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS app_tokens (
  token_hash CHAR(64) PRIMARY KEY, kind VARCHAR(10) NOT NULL, userID INT, email VARCHAR(100), data TEXT,
  created DATETIME NOT NULL, expires DATETIME NOT NULL, KEY email (email), KEY user (userID)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS app_user_prefs (
  userID INT PRIMARY KEY, theme VARCHAR(40)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS app_visits (
  id INT AUTO_INCREMENT PRIMARY KEY, userID INT NOT NULL, username VARCHAR(100) NOT NULL, ip VARCHAR(45) NOT NULL,
  started DATETIME NOT NULL, last_seen DATETIME NOT NULL, KEY user (userID, last_seen), KEY last_seen (last_seen)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS app_watches (
  userID INT NOT NULL, gedcom VARCHAR(20) COLLATE utf8mb3_general_ci NOT NULL, personID VARCHAR(22) COLLATE utf8mb3_general_ci NOT NULL, created_at DATETIME NOT NULL,
  emailed_on DATE, PRIMARY KEY (userID, gedcom, personID), KEY person (gedcom, personID)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
ALTER TABLE app_audit MODIFY record VARCHAR(60) COLLATE utf8mb3_general_ci NOT NULL;
ALTER TABLE app_comments MODIFY gedcom VARCHAR(20) COLLATE utf8mb3_general_ci NOT NULL,
  MODIFY target VARCHAR(50) COLLATE utf8mb3_general_ci NOT NULL;
ALTER TABLE app_face_tags MODIFY gedcom VARCHAR(20) COLLATE utf8mb3_general_ci NOT NULL,
  MODIFY personID VARCHAR(22) COLLATE utf8mb3_general_ci NOT NULL;
ALTER TABLE app_geocode MODIFY gedcom VARCHAR(20) COLLATE utf8mb3_general_ci NOT NULL,
  MODIFY place VARCHAR(248) COLLATE utf8mb3_general_ci NOT NULL;
ALTER TABLE app_link_requests MODIFY personID VARCHAR(22) COLLATE utf8mb3_general_ci NOT NULL;
ALTER TABLE app_watches MODIFY gedcom VARCHAR(20) COLLATE utf8mb3_general_ci NOT NULL,
  MODIFY personID VARCHAR(22) COLLATE utf8mb3_general_ci NOT NULL;
