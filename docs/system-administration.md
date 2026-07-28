# System administration

This guide covers private-LAN deployment, TLS, startup, local account administration, migrations, and recovery. Developer tooling and automated checks are documented in [Local development](local-development.md).

## Deployment prerequisites

Install Docker with Compose v2 and obtain a certificate and private key valid for the private address members will use.

Copy `.env.example` to the untracked `.env` and set:

- `CAREER_APP_BIND_ADDRESS` to an explicit RFC1918 IPv4 or ULA IPv6 address;
- `CAREER_APP_BASE_URL` to the matching HTTPS origin;
- `CAREER_TLS_CERT_FILE` and `CAREER_TLS_PRIVATE_KEY_FILE` to readable absolute paths;
- `CAREER_APP_TIMEZONE` to the installation’s IANA timezone.

Keep `.env`, TLS private keys, database credentials, temporary passwords, and session material untracked. Direct deployments may use `CAREER_DATABASE_URL_FILE` and `CAREER_REDIS_URL_FILE`. Unknown `CAREER_*` application settings fail startup.

The key is mounted read-only into a one-shot initializer, copied into an ephemeral volume mounted only by Nginx, and is not available to FastAPI, workers, or browser code. Nginx remains unprivileged.

## Start and stop the system

Build and start the configured stack:

```sh
make up
```

Only the TLS proxy is published. PostgreSQL, Redis, FastAPI, worker, and beat remain on the private Compose network; there is no cleartext listener or proxy-supplied identity.

Inspect service state and logs:

```sh
docker compose ps
docker compose logs migrate api worker beat proxy
```

Stop services without deleting PostgreSQL data:

```sh
docker compose down
```

## Administrator bootstrap

Create the first account interactively:

```sh
make bootstrap-admin
```

The command refuses to run after any account exists. It prints one generated temporary password once; retain it only long enough to sign in and replace it. The forced-change session permits only session inspection, password change, and logout.

## Account and session administration

Administrators can create accounts, disable or enable them, reset passwords, change administrator status, and revoke sessions under **Accounts**. The service will not disable or demote the final active administrator. Administrator status does not grant access to another member’s career profile.

To revoke sessions without changing a password, use the account detail page and select **Revoke sessions**. Disabling an account also revokes all sessions but preserves its profile.

Reset a local password from the host when browser administration is unavailable:

```sh
docker compose run --rm api career auth reset-password member-name
```

Type `RESET` when prompted. Resetting marks the generated password temporary and revokes every existing session for that account.

## Authentication and privacy controls

FastAPI authenticates case-folded local usernames and Argon2id passwords. Browser sessions use Secure, HttpOnly, SameSite=Lax cookies; only token digests are stored. CSRF tokens remain in memory and authenticated responses are marked `no-store`. Login throttles use hashed username and IP keys in Redis and fail new logins closed when Redis is unavailable.

Every source starts disabled. Copy `config/source-policy.example.yaml` only after terms, robots, authorization, credential-custodian, ownership, and rate-budget review. Never put credentials in source policy.

## Migrations and recovery

Apply migrations independently:

```sh
docker compose run --rm migrate alembic upgrade head
```

The `0002_local_authentication` revision creates accounts, profiles, and sessions. Downgrading below it deletes those records. Back up PostgreSQL and verify the target before running:

```sh
docker compose run --rm migrate alembic downgrade 0001_baseline
docker compose run --rm migrate alembic upgrade head
```

If startup fails, preserve the database volume, inspect `docker compose logs migrate api proxy`, correct configuration or certificate permissions, and rerun `make up`.

## TLS renewal

Replace the certificate and private key at their configured paths, then recreate the proxy and its runtime TLS material:

```sh
docker compose up -d --force-recreate tls-init proxy
```
