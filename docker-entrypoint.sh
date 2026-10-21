#!/bin/sh
set -e

# Apply pending migrations before starting. Set RUN_MIGRATIONS=0 when running
# several replicas and migrate once from a separate job instead.
if [ "$RUN_MIGRATIONS" = "1" ]; then
  echo "Applying database migrations..."
  flask db upgrade
fi

# Load the shipped pages: creates missing ones and updates pages nobody has edited.
# Pages an admin changed are never overwritten here.
if [ "$SEED_CONTENT" = "1" ]; then
  echo "Loading shipped pages..."
  flask seed-content
fi

# First start: create the default admin (DEFAULT_ADMIN_EMAIL, DEFAULT_ADMIN_PASSWORD) when there
# is no admin account yet. Without a password it generates one and prints it here, once.
# Never fatal: the API still starts if this fails (e.g. a password that is too short).
if [ "$CREATE_DEFAULT_ADMIN" = "1" ]; then
  flask admin ensure-default || echo "Warning: the default admin was not created (see above)." >&2
fi

exec "$@"
