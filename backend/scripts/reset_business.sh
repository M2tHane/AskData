#!/usr/bin/env bash
# Wipe business state on the local Compose stack: every meta table except the model configs,
# prompt revisions and the admin account, plus the demo schema and read-only roles in pg-import.
# Dev only; it deletes teams, users, datasources, grants, conversations, audit and usage records.
set -euo pipefail
META=${META_CONTAINER:-askdata-pg-meta-1}
IMPORT=${IMPORT_CONTAINER:-askdata-pg-import-1}

docker exec -i "$IMPORT" psql -U import_admin -d askdata_import -v ON_ERROR_STOP=1 -q <<'SQL'
DO $$ DECLARE s text; r text; BEGIN
  FOR s IN SELECT nspname FROM pg_namespace
           WHERE nspname !~ '^(pg_|information_schema$|public$)' LOOP
    EXECUTE format('DROP SCHEMA %I CASCADE', s);
  END LOOP;
  FOR r IN SELECT rolname FROM pg_roles WHERE rolname !~ '^(pg_|import_)' AND NOT rolsuper LOOP
    EXECUTE format('REVOKE ALL ON DATABASE askdata_import FROM %I', r);
    EXECUTE format('DROP OWNED BY %I', r);
    EXECUTE format('DROP ROLE %I', r);
  END LOOP;
END $$;
SQL

docker exec -i "$META" psql -U askdata -d askdata -v ON_ERROR_STOP=1 -q <<'SQL' 2>&1 | grep -v NOTICE || true
BEGIN;
DO $$ DECLARE t text; BEGIN
  FOR t IN SELECT format('%I', c.relname) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relispartition
      AND c.relname NOT IN ('alembic_version', 'model_config', 'prompt_revision', 'user', 'team')
  LOOP
    EXECUTE 'TRUNCATE TABLE ' || t || ' RESTART IDENTITY CASCADE';
  END LOOP;
END $$;
DELETE FROM "user" WHERE username <> 'admin';
DELETE FROM team;
COMMIT;
SQL
echo "business state cleared"
