"""Real PostgreSQL security regression; never use a default/production DB URL."""

import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import pytest_asyncio

SQL = (
    Path(__file__).parents[2] / "apps/api/alembic/sql/backend_data_api_lockdown.sql"
).read_text()


@pytest_asyncio.fixture
async def db():
    import asyncpg

    url = os.environ.get("SECURITY_TEST_DATABASE_URL")
    assert url, "SECURITY_TEST_DATABASE_URL is required for the security release gate"
    parsed = urlsplit(url)
    assert parsed.hostname in {
        "localhost",
        "127.0.0.1",
        "host.docker.internal",
        "postgres",
    }
    assert parsed.path in {"/ss_test", "/search_sphere_test"}, (
        "Use an isolated synthetic database"
    )
    connection = await asyncpg.connect(url)
    transaction = connection.transaction()
    await transaction.start()
    try:
        for role in ("anon", "authenticated", "service_role"):
            if not await connection.fetchval(
                "SELECT 1 FROM pg_roles WHERE rolname=$1", role
            ):
                bypass = " BYPASSRLS" if role == "service_role" else ""
                await connection.execute(f"CREATE ROLE {role} NOLOGIN{bypass}")
        await connection.execute("""
            CREATE TABLE public.security_fixture (id serial PRIMARY KEY, value text);
            INSERT INTO public.security_fixture(value) VALUES ('synthetic marker');
            GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;
            GRANT ALL ON public.security_fixture TO PUBLIC, anon, authenticated, service_role;
            GRANT SELECT(value) ON public.security_fixture TO anon, authenticated;
            GRANT ALL ON SEQUENCE public.security_fixture_id_seq TO anon, authenticated;
            ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO anon, authenticated;
            ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO anon, authenticated;
            ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO anon, authenticated;
            CREATE SCHEMA IF NOT EXISTS storage;
            CREATE TABLE storage.buckets(id text PRIMARY KEY, public boolean);
            CREATE TABLE storage.objects(id text, bucket_id text);
            INSERT INTO storage.buckets VALUES ('documents', true), ('unrelated', true);
            ALTER TABLE storage.objects ENABLE ROW LEVEL SECURITY;
            CREATE POLICY "Public Access for documents" ON storage.objects FOR ALL
                TO PUBLIC USING (bucket_id='documents') WITH CHECK (bucket_id='documents');
        """)
        yield connection
    finally:
        await transaction.rollback()
        await connection.close()


async def denied(db, role, query):
    import asyncpg

    await db.execute(f"SET LOCAL ROLE {role}")
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with db.transaction():
                await db.execute(query)
    finally:
        await db.execute("RESET ROLE")


@pytest.mark.asyncio
async def test_browser_grants_and_column_acls_are_revoked(db):
    await db.execute(SQL)
    for role in ("anon", "authenticated"):
        await denied(db, role, "SELECT value FROM public.security_fixture LIMIT 0")
        await denied(
            db, role, "INSERT INTO public.security_fixture(value) VALUES ('forged')"
        )
        await denied(db, role, "SELECT nextval('public.security_fixture_id_seq')")
    assert await db.fetchval("SELECT count(*) FROM public.security_fixture") == 1
    async with db.transaction():
        await db.execute("SET LOCAL ROLE service_role")
        assert await db.fetchval("SELECT count(*) FROM public.security_fixture") == 1


@pytest.mark.asyncio
async def test_rls_denies_even_if_a_table_grant_is_reintroduced(db):
    await db.execute(SQL)
    await db.execute("GRANT SELECT ON public.security_fixture TO anon, authenticated")
    for role in ("anon", "authenticated"):
        async with db.transaction():
            await db.execute(f"SET LOCAL ROLE {role}")
            assert (
                await db.fetchval("SELECT count(*) FROM public.security_fixture") == 0
            )
        await db.execute("RESET ROLE")


@pytest.mark.asyncio
async def test_future_objects_do_not_restore_default_browser_access(db):
    await db.execute(SQL)
    await db.execute("""
        CREATE TABLE public.security_future(id serial PRIMARY KEY);
        CREATE FUNCTION public.security_future_fn() RETURNS integer LANGUAGE SQL AS 'SELECT 1';
    """)
    for role in ("anon", "authenticated"):
        await denied(db, role, "SELECT * FROM public.security_future")
        await denied(db, role, "SELECT nextval('public.security_future_id_seq')")
        await denied(db, role, "SELECT public.security_future_fn()")


@pytest.mark.asyncio
async def test_private_bucket_and_idempotency_preserve_other_buckets(db):
    await db.execute(SQL)
    await db.execute(SQL)
    assert (
        await db.fetchval("SELECT public FROM storage.buckets WHERE id='documents'")
        is False
    )
    assert (
        await db.fetchval("SELECT public FROM storage.buckets WHERE id='unrelated'")
        is True
    )
    assert not await db.fetchval("""
        SELECT EXISTS(SELECT 1 FROM pg_policies WHERE schemaname='storage'
            AND policyname='Public Access for documents')
    """)
