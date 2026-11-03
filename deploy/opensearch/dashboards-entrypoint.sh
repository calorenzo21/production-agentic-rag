#!/bin/sh
# Dashboards has no way to read a password from a file, so hand it to this process only.
set -eu

OPENSEARCH_DASHBOARDS_PASSWORD="$(cat /run/secrets/opensearch_dashboards_password)"
OPENSEARCH_DASHBOARDS_COOKIE_PASSWORD="$(cat /run/secrets/opensearch_dashboards_cookie_password)"
export OPENSEARCH_DASHBOARDS_PASSWORD OPENSEARCH_DASHBOARDS_COOKIE_PASSWORD

exec ./opensearch-dashboards-docker-entrypoint.sh "$@"
