#!/bin/sh
set -eu
# Docker Desktop bind mounts may report execution access even for a 0644 file.
# Copy to the container filesystem so PostgreSQL sources the init script reliably.
cp /opt/askdata/init-import.sh /docker-entrypoint-initdb.d/10-import.sh
chmod 0644 /docker-entrypoint-initdb.d/10-import.sh
exec /usr/local/bin/docker-entrypoint.sh postgres
