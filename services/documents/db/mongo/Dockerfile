# MongoDB 7 for the documents service, initialized with the app's collections
# and indexes. init.js runs once, when the data volume is empty. No data.
# Credentials come from the environment at run time:
#   MONGO_INITDB_ROOT_USERNAME, MONGO_INITDB_ROOT_PASSWORD, MONGODB_DB
FROM mongo:7

COPY --chmod=644 init.js /docker-entrypoint-initdb.d/01-init.js

# Run as the image's own non-root user. The image already owns /data/db and
# /data/configdb by this user, and its entrypoint supports starting without root.
USER mongodb

# Ping through the container's own hostname, not localhost: while init.js runs the
# server only listens on localhost, so this stays unhealthy until init is finished
# and MongoDB has restarted for real. Ping needs no credentials.
HEALTHCHECK --interval=5s --timeout=5s --start-period=30s --retries=10 \
    CMD mongosh --quiet --host "$(hostname)" --eval "db.adminCommand('ping').ok" || exit 1
