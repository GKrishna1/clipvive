#!/usr/bin/env bash
set -euo pipefail

PG_CONTAINER="clipvive-postgres-1"
PG_USER="clipvive"
PG_DB="clipvive_db"

echo "Running non-transactional safe migration steps..."

docker exec -i "${PG_CONTAINER}" psql -U "${PG_USER}" -d "${PG_DB}" <<'SQL'
-- 1) Ensure uuid extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 2) Add id UUID column (if missing)
ALTER TABLE job ADD COLUMN IF NOT EXISTS id UUID;

-- 3) Populate id: if job_id looks like UUID use it, else generate new UUID
UPDATE job
SET id = (CASE
    WHEN job_id ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
         THEN job_id::uuid
    ELSE uuid_generate_v4()
END)
WHERE id IS NULL;

-- 4) Make id NOT NULL (safe because we've populated it)
ALTER TABLE job ALTER COLUMN id SET NOT NULL;

-- 5) Create a unique index on id so we can promote later if needed
CREATE UNIQUE INDEX IF NOT EXISTS ux_job_id_uuid ON job(id);

-- 6) Add user_id and copy owner_id -> user_id for existing rows
ALTER TABLE job ADD COLUMN IF NOT EXISTS user_id INTEGER;
UPDATE job SET user_id = owner_id WHERE user_id IS NULL AND owner_id IS NOT NULL;

-- 7) Add rq_id column if missing
ALTER TABLE job ADD COLUMN IF NOT EXISTS rq_id TEXT;

-- 8) Ensure filename TEXT exists (should already)
ALTER TABLE job ADD COLUMN IF NOT EXISTS filename TEXT;

-- 9) Convert size_bytes to BIGINT if not already
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='job' AND column_name='size_bytes'
          AND data_type = 'integer'
    ) THEN
        ALTER TABLE job ALTER COLUMN size_bytes TYPE bigint USING size_bytes::bigint;
    END IF;
END$$;

-- 10) Set default for size_bytes
ALTER TABLE job ALTER COLUMN size_bytes SET DEFAULT 0;

-- 11) Ensure status has a default
ALTER TABLE job ALTER COLUMN status SET DEFAULT 'queued';

-- 12) Convert created_at to timestamptz if it is timestamptz isn't already
DO $$
DECLARE ttype text;
BEGIN
    SELECT data_type INTO ttype FROM information_schema.columns
    WHERE table_name='job' AND column_name='created_at';
    IF ttype = 'timestamp without time zone' THEN
        ALTER TABLE job ALTER COLUMN created_at TYPE timestamptz USING created_at AT TIME ZONE 'UTC';
    END IF;
END$$;

-- 13) If processed_at exists convert type then rename to finished_at
-- Convert to timestamptz then rename
DO $$
DECLARE ttype text;
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='job' AND column_name='processed_at') THEN
        SELECT data_type INTO ttype FROM information_schema.columns
        WHERE table_name='job' AND column_name='processed_at';
        IF ttype = 'timestamp without time zone' THEN
            ALTER TABLE job ALTER COLUMN processed_at TYPE timestamptz USING processed_at AT TIME ZONE 'UTC';
        END IF;
        -- rename to finished_at if finished_at not already exists
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='job' AND column_name='finished_at') THEN
            ALTER TABLE job RENAME COLUMN processed_at TO finished_at;
        END IF;
    ELSE
        -- ensure finished_at exists
        ALTER TABLE job ADD COLUMN IF NOT EXISTS finished_at timestamptz;
    END IF;
END$$;

-- 14) Add FK user_id -> "user"(id) if not present
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        JOIN pg_class t ON c.conrelid = t.oid
        WHERE t.relname = 'job' AND c.contype = 'f'
          AND pg_get_constraintdef(c.oid) ILIKE '%REFERENCES "user"(%'
    ) THEN
        ALTER TABLE job ADD CONSTRAINT job_user_id_fkey FOREIGN KEY (user_id) REFERENCES "user"(id) ON DELETE SET NULL;
    END IF;
END$$;

-- 15) Create helpful indexes
CREATE INDEX IF NOT EXISTS idx_job_user_id ON job(user_id);
CREATE INDEX IF NOT EXISTS idx_job_rq_id ON job(rq_id);

-- Done
SQL

echo "Done. Now show the job table schema:"
docker exec -it "${PG_CONTAINER}" psql -U "${PG_USER}" -d "${PG_DB}" -c "\d+ job"

