-- Backend SQL owns public tables. No browser role is an application SQL client.
-- The owner and service_role retain their existing privileges and RLS bypass.
DO $lockdown$
DECLARE
    relation record;
    column_grant record;
    browser_role text;
    role_sql text;
BEGIN
    FOR relation IN
        SELECT c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
    LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', relation.relname);
    END LOOP;

    FOREACH browser_role IN ARRAY ARRAY['PUBLIC', 'anon', 'authenticated']
    LOOP
        IF browser_role <> 'PUBLIC' AND NOT EXISTS (
            SELECT 1 FROM pg_roles WHERE rolname = browser_role
        ) THEN
            CONTINUE; -- Plain PostgreSQL CI does not create Supabase roles.
        END IF;
        role_sql := CASE WHEN browser_role = 'PUBLIC' THEN 'PUBLIC'
                         ELSE quote_ident(browser_role) END;
        EXECUTE format('REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM %s', role_sql);
        EXECUTE format('REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM %s', role_sql);
        -- Table REVOKE does not remove independent column ACLs.
        FOR column_grant IN
            SELECT c.relname, a.attname FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
              AND a.attnum > 0 AND NOT a.attisdropped AND a.attacl IS NOT NULL
        LOOP
            EXECUTE format('REVOKE ALL PRIVILEGES (%I) ON TABLE public.%I FROM %s',
                column_grant.attname, column_grant.relname, role_sql);
        END LOOP;
        -- Migrations run as the backend owner. Supabase-managed owner defaults
        -- require management access; they cannot be changed by postgres.
        EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM %s', role_sql);
        EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM %s', role_sql);
        EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM %s', role_sql);
        -- PUBLIC function EXECUTE is a global default, not a schema default.
        EXECUTE format('ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM %s', role_sql);
    END LOOP;
END
$lockdown$;

-- Remove only the audited legacy policy; other buckets' policies stay intact.
-- Private Storage still uses the server-only secret/service role through Storage API.
DO $storage$
BEGIN
    IF to_regclass('storage.objects') IS NOT NULL THEN
        DROP POLICY IF EXISTS "Public Access for documents" ON storage.objects;
    END IF;
    IF to_regclass('storage.buckets') IS NOT NULL THEN
        UPDATE storage.buckets SET public = false WHERE id = 'documents';
    END IF;
END
$storage$;

NOTIFY pgrst, 'reload schema';
