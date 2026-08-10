\set ON_ERROR_STOP on

CREATE EXTENSION IF NOT EXISTS vector;

DO $$
BEGIN
    RAISE NOTICE 'PostgreSQL %, pgvector %',
        current_setting('server_version'),
        (SELECT extversion FROM pg_extension WHERE extname = 'vector');
END
$$;
