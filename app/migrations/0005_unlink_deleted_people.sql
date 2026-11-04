-- Deleting a person, or undoing the import that brought them in, left any user linked to them ("This is me")
-- pointing at nobody, and their Me link failed. Those links now go with the person; this clears the old ones.
UPDATE users u SET personID = '', mygedcom = ''
WHERE u.personID <> '' AND NOT EXISTS (SELECT 1 FROM people p WHERE p.personID = u.personID);
