# Deploying to a Linux server

The curator has never been deployed (see [ADR 0001](adr/0001-production-ready-without-deploying.md)). This is the remaining work to run production mode on any Linux server with Docker. Steps that have not been run are marked as untested at the end.

## 1. Prepare the server

Install Docker Engine with the Compose plugin, `make` and `openssl`, then:

```sh
sudo sysctl -w vm.max_map_count=262144   # Also add it to /etc/sysctl.d so that it survives reboots.
```

Allow only ports 80, 443 and your SSH port through the firewall. Allow at least 12 GB of memory: the limits in `compose.prod.yaml` add up to about 8.5 GB and the stack uses about 3 GB when idle.

Clone the repository. Images are built from source on the server, and no registry is involved.

## 2. Point a domain at the server

Create DNS records for the three hosts, using your own domain in place of `example.com`:

| Host | Serves |
|---|---|
| `curator.example.com` | API |
| `airflow.example.com` | Airflow UI |
| `opensearch.example.com` | OpenSearch Dashboards |

Replace `localhost`, `airflow.localhost` and `opensearch.localhost` in `deploy/caddy/Caddyfile` with these hosts. Change `AIRFLOW__API__BASE_URL` in `compose.prod.yaml` to match.

## 3. Use public certificates

In the global block of `deploy/caddy/Caddyfile`, remove `local_certs` and `skip_install_trust`, and add an address for certificate notices:

```
email ops@example.com
```

Caddy then obtains and renews public certificates itself. Port 80 must stay reachable for the challenge.

## 4. Allow only your IP to reach internal tools

The API stays public. Restrict the Airflow UI and OpenSearch Dashboards in `deploy/caddy/Caddyfile`, in addition to their logins:

```
airflow.example.com {
	import hardening
	@denied not remote_ip 203.0.113.10
	respond @denied 403
	reverse_proxy airflow-apiserver:8080
}
```

Do the same for `opensearch.example.com`.

## 5. Install Ollama

Install Ollama natively and pull your models. On Linux, `host.docker.internal` resolves to the Docker bridge, not to the server's loopback, so Ollama must also listen on the bridge. Never bind it to a public interface:

```sh
sudo systemctl edit ollama   # Add: [Service] / Environment="OLLAMA_HOST=172.17.0.1:11434"
sudo systemctl restart ollama
```

If your firewall filters Docker's traffic, allow port 11434 from `172.16.0.0/12` only.

To use Ollama on another machine, set the URL in a `.env` file next to `compose.yaml`. No code changes are needed:

```sh
OLLAMA__URL=http://ollama.internal:11434
```

## 6. Generate secrets and start

```sh
make prod
```

This generates any missing secret in `secrets/` and starts the stack. Copy `secrets/` somewhere safe, because the backups below are of no use for OpenSearch logins and Airflow's encrypted values without it.

Generating a secret again does not change a password that is already in use, because PostgreSQL and OpenSearch only read theirs on first start. To rotate one, change it inside the service first, then replace the file.

The smoke test targets `*.localhost`, so check the deployment by hand:

```sh
curl https://curator.example.com/api/v1/health
```

## 7. Schedule backups

```sh
0 3 * * * cd /path/to/repository && make prod-backup
```

Backups cover the application and Airflow databases. Copy `backups/` off the server. OpenSearch is not backed up: its indexes are rebuilt from PostgreSQL, or from arXiv.

To restore:

```sh
make prod-restore BACKUP=backups/<timestamp>
```

## Untested

The smoke test proves production mode on one machine. These steps have never run, because they need a server and a domain: public certificates, the IP allowlist, DNS, and Ollama on the Docker bridge.
