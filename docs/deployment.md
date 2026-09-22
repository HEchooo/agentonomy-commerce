# Local review deployment

This deployment runs the persistent Agentonomy Commerce review service locally.
It is a reproducible review harness, not a public deployment or an attestation
of ownership or submission eligibility. Settlement is simulated and no real
funds are spent.

The container listens on API port `8080`, published only to
`127.0.0.1:8080` by Compose. The merchant is a real HTTP service inside the
container at `http://127.0.0.1:18081/v1/reconcile`; its port is not published
to the host and it is not a configurable URL proxy. The API uses one worker and
one state directory.

## Start with Docker

Docker and Docker Compose are required. Copy the example environment file and
replace both placeholders before starting:

```bash
cp .env.example .env
# Edit .env so SOURCE_COMMIT is the exact output of `git rev-parse HEAD` and
# AGENTONOMY_API_TOKEN is a fresh token of at least 32 printable characters.
# Do not commit .env.
docker compose --env-file .env up --build -d
```

The `SOURCE_COMMIT` build argument must be exactly the 40-character lowercase
commit that is being reviewed. Compose fails before starting if it is missing,
and the Dockerfile validates it again and bakes it into
`AGENTONOMY_SOURCE_COMMIT`. The API token is required at runtime and is never
baked into the image.

The named `review-data` volume is mounted at `/data`. The non-root process uses
UID/GID `10001:10001`; the image exposes only API port `8080`. Keep the volume
when restarting the container. `docker compose down --volumes` intentionally
deletes the review state and starts a new tenant.

## Start without Docker

Docker is optional for a local review. With the repository's existing virtual
environment, the same API can run directly on `127.0.0.1:8080`:

```bash
export AGENTONOMY_SOURCE_COMMIT="$(git rev-parse HEAD)"
export AGENTONOMY_API_TOKEN="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export AGENTONOMY_STATE_DIR="$PWD/.runtime/review"
export AGENTONOMY_HOST=127.0.0.1
export AGENTONOMY_PORT=8080
make PYTHON=.venv/bin/python review-api
```

Keep those environment variables in the shell used for the verifier. Stop and
start the command again with the same `AGENTONOMY_STATE_DIR` to exercise a
restart without resetting state. This direct launch uses the same loopback HTTP
merchant on port `18081`; it does not create a public endpoint.

## Verify the running service

The standard-library verifier checks the public health and proof documents,
rejects an unauthenticated budget request, creates one synthetic purchase,
checks the `USD 27.50` report, replays it, and confirms that budget and
settlement counters do not change on replay. It reads the bearer token from an
environment variable and writes only sanitized evidence. For Docker, set that
variable to the same token configured in `.env`; for a direct Python launch,
keep the token already generated above. Run this in another terminal with the
same token while the API is running:

```bash
: "${AGENTONOMY_API_TOKEN:?Set the token used by the running service}"
python3 -m scripts.verify_review_api \
  --base-url http://127.0.0.1:8080 \
  --expected-commit "$(git rev-parse HEAD)" \
  --evidence-file /tmp/agentonomy-review-first.json
```

The verifier prints no token, CSV input, receipt secret, wallet key, or Core
identity. Its output contains the purchase and preview IDs needed for a restart
check. After `docker compose restart review-api`, pass those IDs to avoid
creating a second preview or charge:

```bash
python3 -m scripts.verify_review_api \
  --base-url http://127.0.0.1:8080 \
  --expected-commit "$(git rev-parse HEAD)" \
  --purchase-id purchase_FROM_THE_FIRST_RESULT \
  --preview-id preview_FROM_THE_FIRST_RESULT \
  --evidence-file /tmp/agentonomy-review-after-restart.json
```

The verifier is suitable for CI. `--token-env` selects a different environment
variable name when needed; the token is never a command-line argument.

## Review limits and retention

The single tenant starts with a signed simulated budget of `1.00 USDC` and the
CSV offering costs `0.30 USDC` per successful purchase. The initial grant
expires 30 days after it is created. It is stored in the persistent Core state;
restarting the container does not reset, replenish, revoke, or recreate it.
Missing or corrupt persistent metadata prevents startup. An expired or revoked
grant remains retained and causes new purchases to fail closed; it is never
silently replaced. There is no public reset, top-up, grant-creation, or
merchant-URL endpoint.

The API accepts at most 60 authenticated requests per minute by default and
limits request bodies to 256 KiB. Preview input is retained for its five-minute
preview lifetime. Durable purchase results are retained for seven days. Core
and SQLite state remain in `/data` until the volume is deliberately removed.

CSV input must use the columns
`transaction_id,date,description,amount,currency,category`. It is UTF-8 and
at most 128 KiB with at most 1000 data rows, including duplicate rows. Dates
are ISO dates, amounts are signed decimal values with exactly two places and an
absolute value no greater than `1,000,000,000,000`, and every row uses one
three-letter currency. Exact duplicate IDs are reported and counted once;
conflicting duplicate content is rejected. The shipped verification fixture
therefore reports two unique transactions and a net total of `27.50 USD`.

## HTTPS and public hosting

No public hostname, HTTPS reverse proxy, certificate, rate-limit policy beyond
the local API limit, or user-domain configuration is supplied by this
repository. A deployment owner must provide those values and terminate HTTPS
before exposing the API outside the local host. The public health and proof
responses identify the configured source commit and project slug, but they are
not deployment or rights evidence.
