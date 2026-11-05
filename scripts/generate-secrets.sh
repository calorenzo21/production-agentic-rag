#!/bin/sh
# Creates .env and the OpenSearch files in secrets/ when they are missing. Existing ones stay.
#
# secrets/ is private to the current user. The files inside are world-readable because containers
# bind-mount them directly and run as different users, so the directory protects them.
set -eu

cd "$(dirname "$0")/.."
OPENSEARCH_IMAGE=opensearchproject/opensearch:2.19.0
PLACEHOLDER=generated-by-make-setup

password() { openssl rand -hex 24; }
fernet_key() { openssl rand -base64 32 | tr '+/' '-_'; }

if [ ! -f .env ]; then
    while IFS= read -r line; do
        case "$line" in
        AIRFLOW_FERNET_KEY="$PLACEHOLDER") echo "AIRFLOW_FERNET_KEY=$(fernet_key)" ;;
        *"=$PLACEHOLDER") echo "${line%%=*}=$(password)" ;;
        *) echo "$line" ;;
        esac
    done <.env.example >.env.partial
    chmod 600 .env.partial
    mv .env.partial .env
    echo "Generated .env"
fi

# Adds the secrets that .env.example gained after this .env was generated.
while IFS= read -r line; do
    case "$line" in
    *"=$PLACEHOLDER")
        grep -q "^${line%%=*}=" .env || echo "${line%%=*}=$(password)" >>.env
        ;;
    esac
done <.env.example

# shellcheck disable=SC1091
. ./.env

mkdir -p secrets/opensearch
chmod 700 secrets
cd secrets/opensearch

if [ ! -f ca.key ]; then
    openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj "/CN=rag-opensearch-ca" \
        -addext "basicConstraints=critical,CA:TRUE" \
        -addext "keyUsage=critical,keyCertSign,cRLSign" \
        -keyout ca.key -out ca.crt 2>/dev/null
    chmod 400 ca.key
    chmod 444 ca.crt
    rm -f node.crt node.key
    echo "Generated secrets/opensearch/ca.crt"
fi

if [ ! -f node.crt ]; then
    extensions="$(mktemp)"
    cat >"$extensions" <<CONFIG
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth,clientAuth
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid
subjectAltName=DNS:opensearch,DNS:localhost
CONFIG
    openssl req -newkey rsa:2048 -nodes -subj "/CN=opensearch" -keyout node.raw.key \
        -out node.csr 2>/dev/null
    rm -f node.key
    openssl pkcs8 -topk8 -nocrypt -in node.raw.key -out node.key
    openssl x509 -req -in node.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 825 \
        -extfile "$extensions" -out node.crt 2>/dev/null
    rm -f "$extensions" node.raw.key node.csr ca.srl
    chmod 444 node.key node.crt
    echo "Generated secrets/opensearch/node.crt"
fi

# Uses the hashing tool of the OpenSearch image, so it needs Docker running.
hash_password() {
    PASSWORD_TO_HASH="$1" docker run --rm --env PASSWORD_TO_HASH "$OPENSEARCH_IMAGE" \
        plugins/opensearch-security/tools/hash.sh -env PASSWORD_TO_HASH | tail -n 1
}

if [ ! -f internal_users.yml ]; then
    admin_hash="$(hash_password "$OPENSEARCH_ADMIN_PASSWORD")"
    dashboards_hash="$(hash_password "$OPENSEARCH_DASHBOARDS_PASSWORD")"
    app_hash="$(hash_password "$OPENSEARCH_APP_PASSWORD")"
    cat >internal_users.partial <<YAML
_meta:
  type: internalusers
  config_version: 2
admin:
  hash: "$admin_hash"
  reserved: true
  backend_roles:
    - admin
kibanaserver:
  hash: "$dashboards_hash"
  reserved: true
rag_app:
  hash: "$app_hash"
  opendistro_security_roles:
    - rag_app
YAML
    chmod 444 internal_users.partial
    mv internal_users.partial internal_users.yml
    echo "Generated secrets/opensearch/internal_users.yml"
fi
