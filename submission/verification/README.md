# Verification evidence (BLOCKED DRAFT)

Replace the pending values only after the owner supplies a public deployment,
the exact deployed review commit, and approved short-lived review access. The
API base is the HTTPS root origin; capability routes use `/v1/`.

## Prerequisites

- Review commit: `PENDING_REVIEW_COMMIT`
- API base URL: `PENDING_OWNER_INPUT`
- Authentication: export `AGENTONOMY_API_TOKEN` through the approved private channel; never commit the token.

## Health check

```bash
BASE_URL='PENDING_OWNER_INPUT'
export AGENTONOMY_API_TOKEN='<short-lived-review-token>'
curl --fail --silent --show-error "$BASE_URL/health"
```

Expected response:

```json
{"status":"ok","commit":"PENDING_REVIEW_COMMIT"}
```

## Deployment proof

```bash
curl --fail --silent --show-error \
  "$BASE_URL/.well-known/xagent-verification.json"
```

Expected response:

```json
{"schemaVersion":1,"slug":"hechooo-agentonomy-commerce","commit":"PENDING_REVIEW_COMMIT"}
```

## Quote, purchase, read, and replay

The following walkthrough uses the real HTTP merchant path in the local
composition. Settlement is simulated: `real_funds` is always `false` and
`settlement_mode` is `simulated`.

```bash
export CSV='transaction_id,date,description,amount,currency,category
t1,2026-09-01,Hosting,-12.50,USD,software
t2,2026-09-02,Invoice,40.00,USD,revenue
t1,2026-09-01,Hosting,-12.50,USD,software'

curl --fail --silent --show-error "$BASE_URL/v1/services" \
  --header "authorization: Bearer $AGENTONOMY_API_TOKEN"
curl --fail --silent --show-error "$BASE_URL/v1/budget" \
  --header "authorization: Bearer $AGENTONOMY_API_TOKEN"

PREVIEW=$(curl --fail --silent --show-error \
  --request POST "$BASE_URL/v1/previews" \
  --header "authorization: Bearer $AGENTONOMY_API_TOKEN" \
  --header "content-type: application/json" \
  --header "Idempotency-Key: review-preview-1" \
  --data "$(python3 -c 'import json,sys; print(json.dumps(dict(offering_id="csv-reconciliation-v1", csv_text=sys.argv[1])))' "$CSV")")
export PREVIEW_ID="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["preview_id"])' <<<"$PREVIEW")"

PURCHASE=$(curl --fail --silent --show-error \
  --request POST "$BASE_URL/v1/purchases" \
  --header "authorization: Bearer $AGENTONOMY_API_TOKEN" \
  --header "content-type: application/json" \
  --data "$(python3 -c 'import json,sys; print(json.dumps(dict(preview_id=sys.argv[1])))' "$PREVIEW_ID")")
export PURCHASE_ID="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["purchase_id"])' <<<"$PURCHASE")"

curl --fail --silent --show-error \
  "$BASE_URL/v1/purchases/$PURCHASE_ID" \
  --header "authorization: Bearer $AGENTONOMY_API_TOKEN"

# Replay the same preview request. It must return the same preview/purchase
# identity and must not increase the settlement submission count.
REPLAY=$(curl --fail --silent --show-error \
  --request POST "$BASE_URL/v1/previews" \
  --header "authorization: Bearer $AGENTONOMY_API_TOKEN" \
  --header "content-type: application/json" \
  --header "Idempotency-Key: review-preview-1" \
  --data "$(python3 -c 'import json,sys; print(json.dumps(dict(offering_id="csv-reconciliation-v1", csv_text=sys.argv[1])))' "$CSV")")
REPLAY_PURCHASE=$(curl --fail --silent --show-error \
  --request POST "$BASE_URL/v1/purchases" \
  --header "authorization: Bearer $AGENTONOMY_API_TOKEN" \
  --header "content-type: application/json" \
  --data "$(python3 -c 'import json,sys; print(json.dumps(dict(preview_id=sys.argv[1])))' "$PREVIEW_ID")")
curl --fail --silent --show-error \
  "$BASE_URL/v1/purchases/$PURCHASE_ID" \
  --header "authorization: Bearer $AGENTONOMY_API_TOKEN"
REPLAY_BUDGET=$(curl --fail --silent --show-error \
  "$BASE_URL/v1/budget" \
  --header "authorization: Bearer $AGENTONOMY_API_TOKEN")
```

The preview response must identify `csv-reconciliation-v1` and price `0.30`
sandbox USDC. The purchase response must be `state: delivered` with
`service_transport: http`, `real_funds: false`, and
`settlement_mode: simulated`. For the sample CSV, the result has two unique
transactions, duplicate ID `t1`, and USD net total `27.50`. The replay must
preserve the purchase/result, keep budget used at `0.30`, and keep settlement
submissions at one. Compare `REPLAY_BUDGET` with the budget response after the
first purchase to verify that replay did not charge again.

The same sequence and commit binding can be checked with the source verifier:

```bash
.venv/bin/python scripts/verify_review_api.py \
  --base-url "$BASE_URL" \
  --expected-commit "PENDING_REVIEW_COMMIT" \
  --purchase-id "$PURCHASE_ID" \
  --preview-id "$PREVIEW_ID"
```

## Safe failure checks

```bash
# Invalid bearer token: expected HTTP 401; no payment is attempted.
curl --silent --show-error --write-out '%{http_code}\n' --output /dev/null \
  --request GET "$BASE_URL/v1/budget" \
  --header "authorization: Bearer invalid-review-token"

# Missing Idempotency-Key: expected HTTP 422.
curl --silent --show-error --write-out '%{http_code}\n' --output /dev/null \
  --request POST "$BASE_URL/v1/previews" \
  --header "authorization: Bearer $AGENTONOMY_API_TOKEN" \
  --header "content-type: application/json" \
  --data '{"offering_id":"csv-reconciliation-v1","csv_text":"bad"}'
```

The API rejects malformed CSV, bodies over 256 KiB, CSV over 128 KiB or 1,000
rows including duplicates, mixed currencies, and amounts whose absolute value
exceeds 1,000,000,000,000. Expired authorization and unavailable merchant
conditions fail closed without retrying a settled payment.
