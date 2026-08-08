# System administration

This guide covers private-LAN deployment, TLS, startup, local account administration, migrations, and recovery. Developer tooling and automated checks are documented in [Local development](local-development.md).

## Deployment prerequisites

Install Docker with Compose v2 and obtain a certificate and private key valid for the private address members will use.

Copy `.env.example` to the untracked `.env` and set:

- `CAREER_APP_BIND_ADDRESS` to a loopback address for host-only access, or an explicit RFC1918 IPv4 or ULA IPv6 address for LAN access;
- `CAREER_APP_BASE_URL` to the HTTPS origin members will use; its DNS name may differ from the bind address but must resolve to it from intended clients;
- `CAREER_TLS_CERT_FILE` and `CAREER_TLS_PRIVATE_KEY_FILE` to readable absolute paths;
- `CAREER_APP_TIMEZONE` to the installation’s IANA timezone.
- `CAREER_SECURITY_ARTIFACT_KEY_FILE` to a mode-0600 Fernet key file used to
  encrypt imported CVs and evidence excerpts.
- Optional `CAREER_LLM_ENDPOINT`, `CAREER_LLM_MODEL`, and token-budget settings
  enable job enrichment. Set `CAREER_LLM_API_KEY` only in the
  worker environment; the API does not need provider credentials.

Keep `.env`, TLS private keys, database credentials, temporary passwords, and session material untracked. Direct deployments may use `CAREER_DATABASE_URL_FILE` and `CAREER_REDIS_URL_FILE`. Unknown `CAREER_*` application settings fail startup.

Create the artifact key once and back it up with the deployment secrets. Losing
it makes stored artifact content unreadable; the graph’s non-sensitive history
remains in PostgreSQL but affected imports must be reprocessed.

```sh
umask 077
python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())' > /path/to/artifact-fernet.key
```

The key is mounted read-only into a one-shot initializer, copied into an ephemeral volume mounted only by Nginx, and is not available to FastAPI, workers, or browser code. Nginx remains unprivileged.

After an application upgrade that changes CV extraction, reprocess existing
artifacts with the current processor:

```sh
docker compose run --rm api career artifacts reprocess
```

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

Apply a reviewed source policy from the host:

```sh
docker compose run --rm -v "$PWD/config:/policies:ro" api \
  career sources apply-policy /policies/source-policy.yaml
```

The initial external connectors accept bounded RSS/Atom over HTTP(S) and authorized LinkedIn job-alert email from a dedicated Gmail label. Feed acquisition rejects private, link-local, loopback, metadata, credential-bearing, and unsafe redirect destinations. Email acquisition uses verified IMAP TLS, exact sender and link-host allow-lists, bounded message batches, and read-only UID cursors. It never marks, moves, or deletes mail, and it strips LinkedIn tracking parameters from normalized job URLs. XML entities, malformed MIME, attachments, oversized content, and structurally invalid source data fail safely. Schema drift disables only the affected source. Review the safe error and raw capture, update the approved connector configuration or parser fixture, renew the policy if needed, and explicitly enable the source before retrying it from **Operations**.

### Gmail alert connector

Use a dedicated Gmail account and label containing direct LinkedIn Job Alerts messages. Do not forward a personal inbox: the connector validates the real `From` address, ignores unrelated mail, and retains the source message ID plus only `Date`, `From`, and `Subject`. Raw capture contains sanitized extracted card data rather than the RFC 822 envelope; recipient and transport headers, scripts, remote images, footer content, and tracking tokens are discarded. SMTP delivery is not part of this connector.

1. Enable two-step verification on the dedicated Google account and create an app password.
2. Store only that app password in a host file readable by the deployment operator:

```sh
install -m 0600 /dev/null "$HOME/.config/career-assistant/gmail-app-password"
read -rsp 'Gmail app password: ' mail_credential
printf '%s\n' "$mail_credential" > "$HOME/.config/career-assistant/gmail-app-password"
unset mail_credential
chmod 0600 "$HOME/.config/career-assistant/gmail-app-password"
```

3. Set `GMAIL_USERNAME`, `GMAIL_MAILBOX`, and `GMAIL_APP_PASSWORD_FILE` in the untracked `.env`. Start the worker with the mail overlay:

```sh
docker compose -f compose.yaml -f compose.mail.yaml up -d worker
```

4. Copy the disabled `linkedin-alerts` entry from `config/source-policy.example.yaml`, record the authorization/custodian/review evidence, apply it, and enable it only after the Gmail label and allow-lists are verified.
5. Trigger one run from **Operations**. Confirm the run cursor and item counts, then verify that job URLs contain only `https://www.linkedin.com/jobs/view/{id}/` and that unrelated label content was not captured.

Authentication failures use `SOURCE_AUTHENTICATION_FAILED`; malformed or attached content uses `SOURCE_CONTENT_REJECTED`; an unrecognized LinkedIn layout uses `SOURCE_SCHEMA_DRIFT` and disables the source. Rotate the app password by replacing the mounted file and restarting only the worker. Live Gmail canaries are manual because they access an authorized mailbox.

For a reviewed manual source, import a JSON list through the same normalization and deduplication pipeline:

```sh
docker compose run --rm -v "$PWD/imports:/imports:ro" api \
  career sources import manual-source /imports/jobs.json
```

Each item requires `external_id`, `url`, `company_name`, `title`, and `description`; optional Milestone 1 fields include `location`, `remote_policy`, `employment_type`, `posting_date`, `skills`, `responsibilities`, and `benefits`. The file is limited to 1,000 items. Reusing the same file is idempotent, while changed normalized content appends a job version, including when content later returns to an earlier normalized state.

Source runs retain immutable captures and classified errors. A failed run never records its candidate cursor; retrying safely reprocesses already captured items. Raw bodies and secret configuration are not exposed by the browser API.

### AI enrichment

Job enrichment uses the configured OpenAI-compatible chat-completions endpoint.
The application sends one shared normalized job version, labels its content as
untrusted, and requires structured output citing allow-listed job field
locators. Provider/model, prompt/schema hashes, input/output hashes, usage,
latency, request ID, and validation state are retained in reasoning lineage.
Provider failures do not fail the source run; the enrichment task can be
retried with the same idempotency key.

The worker refuses a request that would exceed either
`CAREER_LLM_DAILY_TOKEN_BUDGET` or `CAREER_LLM_TASK_TOKEN_BUDGET`. Keep
provider retention/training terms reviewed before use. Do not put provider
keys in source configuration or expose them through the API.

## Migrations and recovery

Apply migrations independently:

```sh
docker compose run --rm migrate alembic upgrade head
```

The `0002_local_authentication` revision creates accounts, profiles, and sessions. `0003_jobs_and_sources` adds shared acquisition/job history and profile-scoped feedback. `0004_knowledge_graph` adds encrypted profile artifacts, evidence-backed graph data, graph history, proposals, and forced row-level security. `0005_profile_evolution` adds proposal deferrals, observations, suppression state, and decision metadata. `0006_artifact_processing_version` records which CV processor produced derived data. `0007_ai_enrichment` adds immutable prompt versions, shared reasoning lineage, and validated job enrichment. `0008_repeating_job_version_hashes` permits a normalized job state to recur in append-only history. Back up PostgreSQL and verify the target before running:

```sh
docker compose run --rm migrate alembic downgrade 0002_local_authentication
docker compose run --rm migrate alembic upgrade head
```

If startup fails, preserve the database volume, inspect `docker compose logs migrate api proxy`, correct configuration or certificate permissions, and rerun `make up`.

## TLS renewal

Replace the certificate and private key at their configured paths, then recreate the proxy and its runtime TLS material:

```sh
docker compose up -d --force-recreate tls-init proxy
```
