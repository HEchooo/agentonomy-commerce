# Agentonomy Commerce

让 Agent 在用户授权预算内，可靠地购买服务并取得结果。

Agentonomy Commerce contains the Clink runtime for service discovery, spending
mandates, policy checks, budget reservations, payment verification and delivery.
Agents use one `clink_node` MCP entry. The Core controls funds; Marketplace
manages the purchase and returns the result.

## Run locally

Use Python 3.12 for the reproducible environment below.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps -e apps/node
make PYTHON=.venv/bin/python test-commerce
make PYTHON=.venv/bin/python demo
```

The local demonstration uses simulated external settlement. It does not spend
real funds, contact production services or require a wallet key/API key. Core
budget accounting and Marketplace purchase transitions use the shipped business
implementation. See [architecture](docs/architecture.md) for the code boundary.

See [demo instructions and MCP client setup](docs/demo.md) for the full flow,
expected evidence and simulated boundaries.

## Persistent review service

The review API adds a browser walkthrough and a **real HTTP CSV reconciliation
merchant**. It uses the same Core authorization and Marketplace purchase logic.
Settlement is explicitly simulated: each report costs 0.30 sandbox USDC from a
1.00 sandbox USDC budget. No real funds are spent.

Follow [deployment instructions](docs/deployment.md) to launch the authenticated
API locally or build its container. The browser at `/` walks through quote,
purchase, result lookup, and replay. Health and version declarations are public;
all `/v1/` capabilities require a private review token.

State persists across restarts. Replaying a purchase preserves its settlement
and result instead of charging again. Reports are retained for seven days;
the signed bootstrap grant expires after 30 days and is never automatically
replaced. This is one shared review tenant, not a multiuser wallet service.

```sh
make PYTHON=.venv/bin/python test-review test-submission
```

The original stdio MCP demo above is independent and ephemeral. It does not
share the review API's budget or CSV service. The full shipped `clink_node`
runtime remains the Agent entry for Core and Marketplace.

## Hackathon submission

[Submission preparation](submission/README.md) describes the official folder
structure and outstanding owner inputs. The package command exports committed
source with a SHA-256 manifest and reports missing evidence explicitly:

```sh
.venv/bin/python scripts/package_submission.py \
  --output .artifacts/submissions/mcp-hackathon/hechooo-agentonomy-commerce
```

This creates a **blocked draft**, not an official submission. Public HTTPS
deployment, submitter identity/rights, review access and eligibility after the
announced deadline still need owner confirmation. No public deployment or
blockchain settlement is implied by local tests.

See the [two-minute review walkthrough](docs/review-walkthrough.md) for the
demo sequence and the exact boundaries of each claim.

## Existing runtime

The original Node protocol and module names are retained. The full source for
Core, Marketplace, Node, Hosted Facilitator and the USDC executor is included.
Prediction Markets remains as an optional compatibility module and is disabled
for Commerce's local demonstration.

```sh
make PYTHON=.venv/bin/python test-workspace test-node test-e2e
make PYTHON=.venv/bin/python test-apps test-hosted
make test-contracts
```

The existing Personal and Server runtime configuration examples are
`clink.node.example.toml` and `clink.node.server.example.toml`. Connecting real
wallets, RPC endpoints or Hosted signing infrastructure is a separate deployment
step; the local demo does not certify that configuration.

## Source and rights

This is a focused export of the Clink working tree. It has independent Git
history and contains no Clink runtime databases, credentials or private deployment
state. Export hashes are in [source-manifest.json](docs/source-manifest.json).
Original in-file license notices are retained. No additional repository-wide
open-source license has been granted by this export.
