-- Light, dark or (NULL) the device's setting: each user's choice on Your account.
ALTER TABLE app_user_prefs ADD COLUMN IF NOT EXISTS mode VARCHAR(5);
