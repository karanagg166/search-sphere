-- This project has no legitimate table REST/GraphQL consumers. Keep the Data
-- API surface empty, including tables subsequently created by managed owners.
DO $surface$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticator') THEN
        CREATE SCHEMA IF NOT EXISTS pgrst_no_exposed_schemas;
        REVOKE ALL ON SCHEMA pgrst_no_exposed_schemas FROM PUBLIC;
        ALTER ROLE authenticator SET pgrst.db_schemas = 'pgrst_no_exposed_schemas';
    END IF;
END
$surface$;
