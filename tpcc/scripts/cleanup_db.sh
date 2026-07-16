#!/usr/bin/env bash

set -euo pipefail

MYSQL_HOST="127.0.0.1"
MYSQL_PORT="3306"
MYSQL_USER="root"
MYSQL_PASSWORD="mysql_root_password"

DATABASES=(
    "tpcc-candidates"
    "tpcc-baseline"
    "tpcc-deepseekv4flash"
    "tpcc-deepseekv4flashv2"
)

for db in "${DATABASES[@]}"; do
    echo "Dropping database: $db"
    mysql -h "$MYSQL_HOST" -P "$MYSQL_PORT" \
        -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" \
        -e "DROP DATABASE IF EXISTS \`$db\`;"
done

echo "Done."
