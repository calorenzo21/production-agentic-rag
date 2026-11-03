#!/bin/sh
# Creates every missing production secret in secrets/ and leaves existing ones untouched.
#
# The directory is private to the current user. The files inside are world-readable because
# containers bind-mount them directly and run as different users, so the directory protects them.
set -eu

cd "$(dirname "$0")/.."
CADDY_IMAGE=caddy:2.11.7

mkdir -p secrets
chmod 700 secrets
cd secrets

generate() { # name command...
    name="$1"
    shift
    if [ ! -f "$name" ]; then
        "$@" >"$name"
        chmod 444 "$name"
        echo "Generated secrets/$name"
    fi
}

# No trailing newlines: Airflow's _CMD settings use a command's output as it is.
password() { openssl rand -hex 24 | tr -d '\n'; }
fernet_key() { openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n'; }
long_secret() { openssl rand -hex 48 | tr -d '\n'; }

bcrypt() { # plaintext file
    printf '%s\n' "$(cat "$1")" | docker run --interactive --rm "$CADDY_IMAGE" \
        caddy hash-password --algorithm bcrypt | tail -n 1
}

airflow_database_url() {
    printf 'postgresql+psycopg://airflow:%s@postgres:5432/airflow' "$(cat airflow_db_password)"
}

internal_users() {
    cat <<YAML
_meta:
  type: internalusers
  config_version: 2
admin:
  hash: "$(bcrypt opensearch_admin_password)"
  reserved: true
  backend_roles:
    - admin
kibanaserver:
  hash: "$(bcrypt opensearch_dashboards_password)"
  reserved: true
curator:
  hash: "$(bcrypt curator_opensearch_password)"
  opendistro_security_roles:
    - readall_and_monitor
YAML
}

for name in postgres_password curator_db_password airflow_db_password airflow_admin_password \
    opensearch_admin_password curator_opensearch_password opensearch_dashboards_password; do
    generate "$name" password
done
generate airflow_fernet_key fernet_key
generate airflow_jwt_secret long_secret
generate airflow_api_secret_key long_secret
generate opensearch_dashboards_cookie_password long_secret
generate airflow_database_url airflow_database_url
generate opensearch_internal_users.yml internal_users

if [ ! -f opensearch_ca.key ]; then
    openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj "/CN=paper-curator-opensearch-ca" \
        -addext "basicConstraints=critical,CA:TRUE" \
        -addext "keyUsage=critical,keyCertSign,cRLSign" \
        -keyout opensearch_ca.key -out opensearch_ca.crt 2>/dev/null
    chmod 400 opensearch_ca.key
    chmod 444 opensearch_ca.crt
    echo "Generated secrets/opensearch_ca.crt"
fi

if [ ! -f opensearch_node.key ]; then
    extensions="$(mktemp)"
    cat >"$extensions" <<CONFIG
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth,clientAuth
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid
subjectAltName=DNS:opensearch,DNS:localhost
CONFIG
    openssl req -newkey rsa:2048 -nodes -subj "/CN=opensearch" -keyout opensearch_node.raw.key \
        -out opensearch_node.csr 2>/dev/null
    openssl pkcs8 -topk8 -nocrypt -in opensearch_node.raw.key -out opensearch_node.key
    openssl x509 -req -in opensearch_node.csr -CA opensearch_ca.crt -CAkey opensearch_ca.key \
        -CAcreateserial -days 825 -extfile "$extensions" -out opensearch_node.crt 2>/dev/null
    rm -f "$extensions" opensearch_node.raw.key opensearch_node.csr opensearch_ca.srl
    chmod 444 opensearch_node.key opensearch_node.crt
    echo "Generated secrets/opensearch_node.crt"
fi
