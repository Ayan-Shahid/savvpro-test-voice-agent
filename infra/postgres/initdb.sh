#!/bin/sh
# First boot only (docker-entrypoint-initdb.d). Role passwords come from Docker secrets, never from env.
set -eu
s() { cat "/run/secrets/$1"; }
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v dbname="$POSTGRES_DB" \
  -v auth_admin_password="$(s db_auth_password)" \
  -v owner_password="$(s db_owner_password)" \
  -v api_password="$(s db_api_password)" \
  -v worker_password="$(s db_worker_password)" \
  -f /sql/00_roles_and_schemas.sql
