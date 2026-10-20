#!/bin/sh
set -eu
# Runs only on first initialization of the import cluster. No query login can
# CREATE a schema/table or write data; each ds_<id>_reader is provisioned by core.importer.
psql -X -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=writer_password="$IMPORT_WRITER_PASSWORD" <<'SQL'
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON DATABASE askdata_import FROM PUBLIC;
CREATE ROLE import_writer LOGIN NOSUPERUSER NOCREATEDB CREATEROLE NOINHERIT
    PASSWORD :'writer_password';
GRANT CONNECT ON DATABASE askdata_import TO import_writer WITH GRANT OPTION;
GRANT CREATE ON DATABASE askdata_import TO import_writer;
SQL
