-- An empty database for a new site: the family tree's tables (their layout follows TNG's, which Kith grew out of),
-- with the event and media types every tree needs. Loaded by `flask --app app setup db`; the site's own app_
-- tables and later changes come from app/migrations.
CREATE TABLE `addresses` (
  `addressID` int(11) NOT NULL AUTO_INCREMENT,
  `address1` varchar(64) NOT NULL,
  `address2` varchar(64) NOT NULL,
  `city` varchar(64) NOT NULL,
  `state` varchar(64) NOT NULL,
  `zip` varchar(10) NOT NULL,
  `country` varchar(64) NOT NULL,
  `www` varchar(100) NOT NULL,
  `email` varchar(100) NOT NULL,
  `phone` varchar(30) NOT NULL,
  `gedcom` varchar(20) NOT NULL,
  PRIMARY KEY (`addressID`),
  KEY `address` (`gedcom`,`country`,`state`,`city`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;
CREATE TABLE `branches` (
  `branch` varchar(20) NOT NULL,
  `gedcom` varchar(20) NOT NULL,
  `description` varchar(128) NOT NULL,
  `personID` varchar(22) NOT NULL,
  `agens` int(11) NOT NULL,
  `dgens` int(11) NOT NULL,
  `dagens` int(11) NOT NULL,
  `inclspouses` tinyint(4) NOT NULL,
  `action` tinyint(4) NOT NULL,
  PRIMARY KEY (`gedcom`,`branch`),
  KEY `description` (`gedcom`,`description`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;
CREATE TABLE `branchlinks` (
  `ID` int(11) NOT NULL AUTO_INCREMENT,
  `branch` varchar(20) NOT NULL,
  `gedcom` varchar(20) NOT NULL,
  `persfamID` varchar(22) NOT NULL,
  PRIMARY KEY (`ID`),
  UNIQUE KEY `branch` (`gedcom`,`branch`,`persfamID`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;
CREATE TABLE `cemeteries` (
  `cemeteryID` int(11) NOT NULL AUTO_INCREMENT,
  `cemname` varchar(64) NOT NULL,
  `maplink` varchar(255) NOT NULL,
  `city` varchar(64) DEFAULT NULL,
  `county` varchar(64) DEFAULT NULL,
  `state` varchar(64) DEFAULT NULL,
  `country` varchar(64) DEFAULT NULL,
  `longitude` varchar(22) DEFAULT NULL,
  `latitude` varchar(22) DEFAULT NULL,
  `zoom` tinyint(4) DEFAULT NULL,
  `notes` text DEFAULT NULL,
  `place` varchar(248) NOT NULL,
  PRIMARY KEY (`cemeteryID`),
  KEY `cemname` (`cemname`),
  KEY `place` (`place`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;
CREATE TABLE `children` (
  `ID` int(11) NOT NULL AUTO_INCREMENT,
  `gedcom` varchar(20) NOT NULL,
  `familyID` varchar(22) NOT NULL,
  `personID` varchar(22) NOT NULL,
  `frel` varchar(20) NOT NULL,
  `mrel` varchar(20) NOT NULL,
  `sealdate` varchar(50) NOT NULL,
  `sealdatetr` date NOT NULL,
  `sealplace` text NOT NULL,
  `haskids` tinyint(4) NOT NULL,
  `ordernum` smallint(6) NOT NULL,
  `parentorder` tinyint(4) NOT NULL,
  PRIMARY KEY (`ID`),
  UNIQUE KEY `familyID` (`gedcom`,`familyID`,`personID`),
  KEY `personID` (`gedcom`,`personID`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;
CREATE TABLE `citations` (
  `citationID` int(11) NOT NULL AUTO_INCREMENT,
  `gedcom` varchar(20) NOT NULL,
  `persfamID` varchar(22) NOT NULL,
  `eventID` varchar(10) NOT NULL,
  `sourceID` varchar(22) NOT NULL,
  `ordernum` float NOT NULL,
  `description` text NOT NULL,
  `citedate` varchar(50) NOT NULL,
  `citedatetr` date NOT NULL,
  `citetext` text NOT NULL,
  `page` text NOT NULL,
  `quay` varchar(2) NOT NULL,
  `note` text NOT NULL,
  PRIMARY KEY (`citationID`),
  KEY `citation` (`gedcom`,`persfamID`,`eventID`,`sourceID`,`description`(20))
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;
CREATE TABLE `events` (
  `eventID` int(11) NOT NULL AUTO_INCREMENT,
  `gedcom` varchar(20) NOT NULL,
  `persfamID` varchar(22) NOT NULL,
  `eventtypeID` int(11) NOT NULL,
  `eventdate` varchar(50) NOT NULL,
  `eventdatetr` date NOT NULL,
  `eventplace` text NOT NULL,
  `age` varchar(12) NOT NULL,
  `agency` varchar(120) NOT NULL,
  `cause` varchar(90) NOT NULL,
  `addressID` varchar(10) NOT NULL,
  `parenttag` varchar(10) NOT NULL,
  `info` text NOT NULL,
  PRIMARY KEY (`eventID`),
  KEY `persfamID` (`gedcom`,`persfamID`),
  KEY `eventplace` (`gedcom`,`eventplace`(20))
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;
CREATE TABLE `eventtypes` (
  `eventtypeID` int(11) NOT NULL AUTO_INCREMENT,
  `tag` varchar(10) NOT NULL,
  `description` varchar(90) NOT NULL,
  `display` text NOT NULL,
  `keep` tinyint(4) NOT NULL,
  `collapse` tinyint(4) NOT NULL,
  `ordernum` smallint(6) NOT NULL,
  `ldsevent` tinyint(4) NOT NULL,
  `type` char(1) NOT NULL,
  PRIMARY KEY (`eventtypeID`),
  UNIQUE KEY `typetagdesc` (`type`,`tag`,`description`),
  KEY `ordernum` (`ordernum`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;
CREATE TABLE `families` (
  `ID` int(11) NOT NULL AUTO_INCREMENT,
  `gedcom` varchar(20) NOT NULL,
  `familyID` varchar(22) NOT NULL,
  `husband` varchar(22) NOT NULL,
  `wife` varchar(22) NOT NULL,
  `marrdate` varchar(50) NOT NULL,
  `marrdatetr` date NOT NULL,
  `marrplace` text NOT NULL,
  `marrtype` varchar(90) NOT NULL,
  `divdate` varchar(50) NOT NULL,
  `divdatetr` date NOT NULL,
  `divplace` text NOT NULL,
  `status` varchar(20) NOT NULL,
  `sealdate` varchar(50) NOT NULL,
  `sealdatetr` date NOT NULL,
  `sealplace` text NOT NULL,
  `husborder` tinyint(4) NOT NULL,
  `wifeorder` tinyint(4) NOT NULL,
  `changedate` datetime NOT NULL,
  `living` tinyint(4) NOT NULL,
  `private` tinyint(4) NOT NULL,
  `branch` varchar(512) NOT NULL,
  `changedby` varchar(100) NOT NULL,
  `edituser` varchar(100) NOT NULL,
  `edittime` int(11) NOT NULL,
  PRIMARY KEY (`ID`),
  UNIQUE KEY `familyID` (`gedcom`,`familyID`),
  KEY `husband` (`gedcom`,`husband`),
  KEY `wife` (`gedcom`,`wife`),
  KEY `marrplace` (`marrplace`(20)),
  KEY `divplace` (`divplace`(20)),
  KEY `changedate` (`changedate`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;
CREATE TABLE `media` (
  `mediaID` int(11) NOT NULL AUTO_INCREMENT,
  `mediatypeID` varchar(20) NOT NULL,
  `mediakey` varchar(255) NOT NULL,
  `gedcom` varchar(20) NOT NULL,
  `form` varchar(10) NOT NULL,
  `path` varchar(255) DEFAULT NULL,
  `description` text DEFAULT NULL,
  `datetaken` varchar(50) DEFAULT NULL,
  `placetaken` text DEFAULT NULL,
  `notes` text DEFAULT NULL,
  `owner` text DEFAULT NULL,
  `thumbpath` varchar(255) DEFAULT NULL,
  `alwayson` tinyint(4) DEFAULT NULL,
  `map` text DEFAULT NULL,
  `abspath` tinyint(4) DEFAULT NULL,
  `status` varchar(40) DEFAULT NULL,
  `showmap` smallint(6) DEFAULT NULL,
  `cemeteryID` int(11) DEFAULT NULL,
  `plot` text DEFAULT NULL,
  `linktocem` tinyint(4) DEFAULT NULL,
  `longitude` varchar(22) DEFAULT NULL,
  `latitude` varchar(22) DEFAULT NULL,
  `zoom` tinyint(4) DEFAULT NULL,
  `width` smallint(6) DEFAULT NULL,
  `height` smallint(6) DEFAULT NULL,
  `left_value` smallint(6) NOT NULL,
  `top_value` smallint(6) NOT NULL,
  `bodytext` text DEFAULT NULL,
  `usenl` tinyint(4) DEFAULT NULL,
  `newwindow` tinyint(4) DEFAULT NULL,
  `usecollfolder` tinyint(4) DEFAULT NULL,
  `private` tinyint(4) NOT NULL,
  `changedate` datetime NOT NULL,
  `changedby` varchar(100) NOT NULL,
  PRIMARY KEY (`mediaID`),
  UNIQUE KEY `mediakey` (`gedcom`,`mediakey`),
  KEY `mediatypeID` (`mediatypeID`),
  KEY `changedate` (`changedate`),
  KEY `description` (`description`(20)),
  KEY `headstones` (`cemeteryID`,`description`(20))
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;
CREATE TABLE `medialinks` (
  `medialinkID` int(11) NOT NULL AUTO_INCREMENT,
  `gedcom` varchar(20) NOT NULL,
  `linktype` char(1) NOT NULL,
  `personID` varchar(248) NOT NULL,
  `eventID` varchar(10) NOT NULL,
  `mediaID` int(11) NOT NULL,
  `altdescription` text NOT NULL,
  `altnotes` text NOT NULL,
  `ordernum` float NOT NULL,
  `dontshow` tinyint(4) NOT NULL,
  `defphoto` varchar(1) NOT NULL,
  `ctop` smallint(6) NOT NULL,
  `cleft` smallint(6) NOT NULL,
  `cwidth` smallint(6) NOT NULL,
  `cheight` smallint(6) NOT NULL,
  PRIMARY KEY (`medialinkID`),
  UNIQUE KEY `mediaID` (`gedcom`,`personID`,`mediaID`,`eventID`),
  KEY `personID` (`gedcom`,`personID`,`ordernum`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;
CREATE TABLE `mediatypes` (
  `mediatypeID` varchar(20) NOT NULL,
  `display` varchar(40) NOT NULL,
  `path` varchar(127) NOT NULL,
  `liketype` varchar(20) NOT NULL,
  `icon` varchar(50) NOT NULL,
  `thumb` varchar(50) NOT NULL,
  `exportas` varchar(20) NOT NULL,
  `disabled` tinyint(4) NOT NULL,
  `ordernum` tinyint(4) NOT NULL,
  `localpath` varchar(250) NOT NULL,
  `whatsnew` smallint(6) NOT NULL,
  `statistics` smallint(6) NOT NULL,
  `menus` smallint(6) NOT NULL,
  PRIMARY KEY (`mediatypeID`),
  KEY `ordernum` (`ordernum`,`display`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;
CREATE TABLE `notelinks` (
  `ID` int(11) NOT NULL AUTO_INCREMENT,
  `persfamID` varchar(22) NOT NULL,
  `gedcom` varchar(20) NOT NULL,
  `xnoteID` int(11) NOT NULL,
  `eventID` varchar(10) NOT NULL,
  `ordernum` float NOT NULL,
  `secret` tinyint(4) NOT NULL,
  PRIMARY KEY (`ID`),
  KEY `notelinks` (`gedcom`,`persfamID`,`eventID`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;
CREATE TABLE `people` (
  `ID` int(11) NOT NULL AUTO_INCREMENT,
  `gedcom` varchar(20) NOT NULL,
  `personID` varchar(22) NOT NULL,
  `lnprefix` varchar(25) NOT NULL,
  `lastname` varchar(127) NOT NULL,
  `firstname` varchar(127) NOT NULL,
  `birthdate` varchar(50) NOT NULL,
  `birthdatetr` date NOT NULL,
  `sex` varchar(25) NOT NULL,
  `birthplace` text NOT NULL,
  `deathdate` varchar(50) NOT NULL,
  `deathdatetr` date NOT NULL,
  `deathplace` text NOT NULL,
  `altbirthtype` varchar(5) NOT NULL,
  `altbirthdate` varchar(50) NOT NULL,
  `altbirthdatetr` date NOT NULL,
  `altbirthplace` text NOT NULL,
  `burialdate` varchar(50) NOT NULL,
  `burialdatetr` date NOT NULL,
  `burialplace` text NOT NULL,
  `burialtype` tinyint(4) NOT NULL,
  `baptdate` varchar(50) NOT NULL,
  `baptdatetr` date NOT NULL,
  `baptplace` text NOT NULL,
  `confdate` varchar(50) NOT NULL,
  `confdatetr` date NOT NULL,
  `confplace` text NOT NULL,
  `initdate` varchar(50) NOT NULL,
  `initdatetr` date NOT NULL,
  `initplace` text NOT NULL,
  `endldate` varchar(50) NOT NULL,
  `endldatetr` date NOT NULL,
  `endlplace` text NOT NULL,
  `changedate` datetime NOT NULL,
  `nickname` text NOT NULL,
  `title` tinytext NOT NULL,
  `prefix` tinytext NOT NULL,
  `suffix` tinytext NOT NULL,
  `nameorder` tinyint(4) NOT NULL,
  `famc` varchar(22) NOT NULL,
  `metaphone` varchar(15) NOT NULL,
  `living` tinyint(4) NOT NULL,
  `private` tinyint(4) NOT NULL,
  `branch` varchar(512) NOT NULL,
  `changedby` varchar(100) NOT NULL,
  `edituser` varchar(100) NOT NULL,
  `edittime` int(11) NOT NULL,
  PRIMARY KEY (`ID`),
  UNIQUE KEY `gedpers` (`gedcom`,`personID`),
  KEY `lastname` (`lastname`,`firstname`),
  KEY `firstname` (`firstname`),
  KEY `gedlast` (`gedcom`,`lastname`,`firstname`),
  KEY `gedfirst` (`gedcom`,`firstname`),
  KEY `birthplace` (`birthplace`(20)),
  KEY `altbirthplace` (`altbirthplace`(20)),
  KEY `deathplace` (`deathplace`(20)),
  KEY `burialplace` (`burialplace`(20)),
  KEY `baptplace` (`baptplace`(20)),
  KEY `confplace` (`confplace`(20)),
  KEY `initplace` (`initplace`(20)),
  KEY `endlplace` (`endlplace`(20)),
  KEY `changedate` (`changedate`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;
CREATE TABLE `places` (
  `ID` int(11) NOT NULL AUTO_INCREMENT,
  `gedcom` varchar(20) NOT NULL,
  `place` varchar(248) NOT NULL,
  `longitude` varchar(22) DEFAULT NULL,
  `latitude` varchar(22) DEFAULT NULL,
  `zoom` tinyint(4) DEFAULT NULL,
  `placelevel` tinyint(4) DEFAULT NULL,
  `temple` tinyint(4) NOT NULL,
  `geoignore` tinyint(4) NOT NULL,
  `notes` text DEFAULT NULL,
  `changedate` datetime NOT NULL,
  `changedby` varchar(100) NOT NULL,
  PRIMARY KEY (`ID`),
  UNIQUE KEY `place` (`gedcom`,`place`),
  KEY `temple` (`temple`,`gedcom`,`place`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;
CREATE TABLE `repositories` (
  `ID` int(11) NOT NULL AUTO_INCREMENT,
  `repoID` varchar(22) NOT NULL,
  `reponame` varchar(90) NOT NULL,
  `gedcom` varchar(20) NOT NULL,
  `addressID` int(11) NOT NULL,
  `changedate` datetime NOT NULL,
  `changedby` varchar(100) NOT NULL,
  PRIMARY KEY (`ID`),
  UNIQUE KEY `repoID` (`gedcom`,`repoID`),
  KEY `reponame` (`reponame`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;
CREATE TABLE `sources` (
  `ID` int(11) NOT NULL AUTO_INCREMENT,
  `gedcom` varchar(20) NOT NULL,
  `sourceID` varchar(22) NOT NULL,
  `callnum` varchar(120) NOT NULL,
  `type` varchar(20) DEFAULT NULL,
  `title` text NOT NULL,
  `author` text NOT NULL,
  `publisher` text NOT NULL,
  `other` text NOT NULL,
  `shorttitle` text NOT NULL,
  `comments` text DEFAULT NULL,
  `actualtext` text NOT NULL,
  `repoID` varchar(22) NOT NULL,
  `changedate` datetime NOT NULL,
  `changedby` varchar(100) NOT NULL,
  PRIMARY KEY (`ID`),
  UNIQUE KEY `sourceID` (`gedcom`,`sourceID`),
  KEY `changedate` (`changedate`),
  FULLTEXT KEY `sourcetext` (`actualtext`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;
CREATE TABLE `timelineevents` (
  `tleventID` int(11) NOT NULL AUTO_INCREMENT,
  `evday` tinyint(4) NOT NULL,
  `evmonth` tinyint(4) NOT NULL,
  `evyear` varchar(10) NOT NULL,
  `endday` tinyint(4) NOT NULL,
  `endmonth` tinyint(4) NOT NULL,
  `endyear` varchar(10) NOT NULL,
  `evtitle` varchar(128) NOT NULL,
  `evdetail` text NOT NULL,
  PRIMARY KEY (`tleventID`),
  KEY `evyear` (`evyear`,`evmonth`,`evday`,`evdetail`(100)),
  KEY `evdetail` (`evdetail`(100))
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;
CREATE TABLE `users` (
  `userID` int(11) NOT NULL AUTO_INCREMENT,
  `description` varchar(50) NOT NULL,
  `username` varchar(100) NOT NULL,
  `password` varchar(128) NOT NULL,
  `password_type` varchar(10) NOT NULL,
  `gedcom` varchar(128) DEFAULT NULL,
  `mygedcom` varchar(20) NOT NULL,
  `personID` varchar(22) NOT NULL,
  `role` varchar(15) NOT NULL,
  `allow_edit` tinyint(4) NOT NULL,
  `allow_add` tinyint(4) NOT NULL,
  `tentative_edit` tinyint(4) NOT NULL,
  `allow_delete` tinyint(4) NOT NULL,
  `allow_lds` tinyint(4) NOT NULL,
  `allow_ged` tinyint(4) NOT NULL,
  `allow_pdf` tinyint(4) NOT NULL,
  `allow_living` tinyint(4) NOT NULL,
  `allow_private` tinyint(4) NOT NULL,
  `allow_private_notes` tinyint(4) NOT NULL,
  `allow_private_media` tinyint(4) NOT NULL,
  `allow_profile` tinyint(4) NOT NULL,
  `branch` varchar(20) DEFAULT NULL,
  `realname` varchar(50) DEFAULT NULL,
  `phone` varchar(30) DEFAULT NULL,
  `email` varchar(50) DEFAULT NULL,
  `address` varchar(100) DEFAULT NULL,
  `city` varchar(64) DEFAULT NULL,
  `state` varchar(64) DEFAULT NULL,
  `zip` varchar(10) DEFAULT NULL,
  `country` varchar(64) DEFAULT NULL,
  `website` varchar(128) DEFAULT NULL,
  `languageID` smallint(6) NOT NULL,
  `lastlogin` datetime NOT NULL,
  `disabled` tinyint(4) NOT NULL,
  `dt_registered` datetime NOT NULL,
  `dt_activated` datetime NOT NULL,
  `dt_consented` datetime NOT NULL,
  `no_email` tinyint(4) DEFAULT NULL,
  `notes` text DEFAULT NULL,
  `reset_pwd_code` varchar(30) NOT NULL,
  PRIMARY KEY (`userID`),
  UNIQUE KEY `username` (`username`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;
CREATE TABLE `xnotes` (
  `ID` int(11) NOT NULL AUTO_INCREMENT,
  `noteID` varchar(22) NOT NULL,
  `gedcom` varchar(20) NOT NULL,
  `note` text NOT NULL,
  PRIMARY KEY (`ID`),
  KEY `noteID` (`gedcom`,`noteID`),
  FULLTEXT KEY `note` (`note`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;
SET @OLD_AUTOCOMMIT=@@AUTOCOMMIT, @@AUTOCOMMIT=0;
INSERT INTO `eventtypes` VALUES (1,'OCCU','','Occupation',1,0,0,0,'I');
INSERT INTO `eventtypes` VALUES (2,'BAPM','','Baptism',1,0,0,0,'I');
INSERT INTO `eventtypes` VALUES (3,'EDUC','','Education',1,0,0,0,'I');
INSERT INTO `eventtypes` VALUES (4,'REFN','','Reference Number',1,0,0,0,'I');
INSERT INTO `eventtypes` VALUES (5,'ADDR','','Address',1,0,0,0,'I');
INSERT INTO `eventtypes` VALUES (6,'DEAT','','Death',1,0,0,0,'I');
INSERT INTO `eventtypes` VALUES (7,'_MILI','','_MILI',1,0,0,0,'I');
INSERT INTO `eventtypes` VALUES (8,'SSN','','Social Security Number',1,0,0,0,'I');
INSERT INTO `eventtypes` VALUES (9,'RELI','','Religion',1,0,0,0,'I');
INSERT INTO `eventtypes` VALUES (10,'IMMI','','Immigration',1,0,0,0,'I');
INSERT INTO `eventtypes` VALUES (11,'DSCR','','Physical Description',1,0,0,0,'I');
INSERT INTO `eventtypes` VALUES (12,'WILL','','Will',1,0,0,0,'I');
INSERT INTO `eventtypes` VALUES (13,'ADOP','','Adopted',1,0,0,0,'I');
INSERT INTO `eventtypes` VALUES (14,'CENS','','Census',1,0,0,0,'I');
INSERT INTO `eventtypes` VALUES (15,'EVEN','','Event',1,0,0,0,'I');
INSERT INTO `eventtypes` VALUES (16,'EMAIL','','EMAIL',1,0,0,0,'I');
INSERT INTO `eventtypes` VALUES (17,'FCOM','','First Communion',1,0,0,0,'I');
INSERT INTO `eventtypes` VALUES (18,'CREM','','Cremated',1,0,0,0,'I');
INSERT INTO `eventtypes` VALUES (19,'PROP','','Possessions',1,0,0,0,'I');
INSERT INTO `eventtypes` VALUES (20,'BIRT','','Birth',1,0,0,0,'I');
INSERT INTO `eventtypes` VALUES (21,'TITL','','Title',1,0,0,0,'I');
INSERT INTO `eventtypes` VALUES (22,'_EMAIL','','_EMAIL',1,0,0,0,'I');
INSERT INTO `eventtypes` VALUES (23,'GRAD','','Graduation',1,0,0,0,'I');
INSERT INTO `eventtypes` VALUES (24,'SEX','','SEX',1,0,0,0,'I');
INSERT INTO `eventtypes` VALUES (25,'BURI','','Burial',1,0,0,0,'I');
INSERT INTO `eventtypes` VALUES (26,'RFN','','Permanent Record Number',1,0,0,0,'I');
INSERT INTO `eventtypes` VALUES (27,'DIVF','','Divorce Filed',1,0,0,0,'F');
INSERT INTO `eventtypes` VALUES (28,'MARL','','Marriage License',1,0,0,0,'F');
INSERT INTO `eventtypes` VALUES (29,'MARR','','Married',1,0,0,0,'F');
INSERT INTO `eventtypes` VALUES (30,'CENS','','Census',1,0,0,0,'F');
INSERT INTO `eventtypes` VALUES (31,'URL','','URL',1,0,0,0,'S');
INSERT INTO `eventtypes` VALUES (32,'_DBID','','_DBID',1,0,0,0,'S');
INSERT INTO `eventtypes` VALUES (33,'EMAIL','','EMAIL',1,0,0,0,'R');
INSERT INTO `eventtypes` VALUES (34,'WWW','','WWW',1,0,0,0,'R');
COMMIT;
SET AUTOCOMMIT=@OLD_AUTOCOMMIT;
SET @OLD_AUTOCOMMIT=@@AUTOCOMMIT, @@AUTOCOMMIT=0;
INSERT INTO `mediatypes` VALUES ('photos','','','','','','',0,-6,'',1,1,1);
INSERT INTO `mediatypes` VALUES ('documents','','','','','','',0,-5,'',1,1,1);
INSERT INTO `mediatypes` VALUES ('headstones','','','','','','',0,-4,'',1,1,1);
INSERT INTO `mediatypes` VALUES ('histories','','','','','','',0,-3,'',1,1,1);
INSERT INTO `mediatypes` VALUES ('recordings','','','','','','',0,-2,'',1,1,1);
INSERT INTO `mediatypes` VALUES ('videos','','','','','','',0,-1,'',1,1,1);
COMMIT;
SET AUTOCOMMIT=@OLD_AUTOCOMMIT;
