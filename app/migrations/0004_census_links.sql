-- Census phase 3: an editor's answer for one line of a census transcription (events.info): the person it is, or
-- NULL for "none of these". line_text is the line as it was then; if the transcription changes, the answer lapses.
CREATE TABLE IF NOT EXISTS app_census_links (
  gedcom VARCHAR(20) NOT NULL, eventID INT NOT NULL, line SMALLINT NOT NULL, line_text VARCHAR(255) NOT NULL,
  personID VARCHAR(22) COLLATE utf8mb3_general_ci, decided_by VARCHAR(100) NOT NULL, decided_at DATETIME NOT NULL,
  PRIMARY KEY (gedcom, eventID, line), KEY person (gedcom, personID)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
