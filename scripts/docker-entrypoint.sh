#!/bin/sh
set -e

chown -R studylens:studylens /app/data 2>/dev/null || true

exec gosu studylens "$@"
