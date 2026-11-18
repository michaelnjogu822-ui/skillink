-- Create the SkillLink database and a least-privilege application user.
--
-- Run as a MySQL administrator, for example:
--   mysql -u root -p < scripts/mysql_setup.sql
--
-- Replace the password before running this on anything other than a local
-- machine, and keep it in the gitignored .env, never in a committed file.

CREATE DATABASE IF NOT EXISTS skilllink
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'skilllink'@'localhost'
    IDENTIFIED BY 'replace-this-app-password';

-- The application only needs data access on its own schema. It never needs
-- CREATE USER, GRANT, FILE, or access to other databases.
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, REFERENCES, DROP
    ON skilllink.* TO 'skilllink'@'localhost';

FLUSH PRIVILEGES;

-- Verify:
--   SHOW GRANTS FOR 'skilllink'@'localhost';
