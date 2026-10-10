# Agentonomy Commerce

让 Agent 在用户授权预算内，可靠地购买服务并取得结果。

Agentonomy Commerce lets an AI agent buy a service under a budget that the user
has authorized. The system checks the quote, reserves the budget, verifies the
payment independently, asks the merchant to validate the receipt, and returns
the result to the user. The user never gives the Agent a private key.

Wallet authorization site: [review.agentonomy.xyz](https://review.agentonomy.xyz)
· Monad testnet · valueless test assets. The wallet site and external MCP flow
are deployed publicly. Fresh own-wallet purchase acceptance is still pending;
deployment and signer readiness do not establish a completed purchase.

## The concrete example

The current service is CSV reconciliation. It costs **0.30 test-only,
valueless TestUSD** per report. The owner authorization used by the example is
explicit and finite:

- the wallet claims **1.00 TestUSD**;
- the Core grant allows **1.00 total**, **0.50 per payment**, for **one day**;
- the wallet approves **BudgetExecutor to spend up to 1.00 TestUSD**;
- the Agent can request the fixed **0.30 TestUSD** quote for a report.

The allowance, grant, policy decision, reservation, payment proof, and delivery
are separate records. A replay returns the original order and result instead of
charging again.

The report returns income, expense, net, and category totals. A completed order
records its purchase ID, payment transaction hash, and output hash.

## Architecture

Core is the sole authority for wallet identity, signed spending grants,
policy/risk decisions, funding, reservations, and audit. Marketplace owns
service discovery, quotes, orders, and delivery. The Agent uses one unified MCP
Agent Gateway to request commerce; it cannot call Core or a merchant directly.

The Agent is the user's Codex or another MCP client. A local device bridge
connects it to the hosted MCP endpoint. The wallet site only manages wallet
binding, spending authorization, device consent, and revocation. Service search,
quotes, purchases, and reports stay in the Agent conversation.

```mermaid
flowchart TB
  %% Logical component dependencies; arrows are not a transport trace.
  subgraph device[User device]
    A[Codex / MCP client]
    D[Local device bridge]
    W[Buyer wallet]
    U[Wallet authorization site]
  end

  subgraph services[Hosted commerce services]
    G[Authenticated MCP Agent Gateway]
    M[Marketplace]
    I[Service identity guard]
    R[HTTP merchant]
  end

  subgraph settlement[Core and chain settlement]
    C[Core]
    X[Isolated signer / gas relayer]
    B[BudgetExecutor contract]
    T[TestUSD token]
    V[Independent RPC watcher]
  end

  W -->|login + grant + install consent| U
  W -->|approve BudgetExecutor| T
  U -->|verify wallet, consent, grant| C
  C -.->|short-lived device authorization| D
  A --> D --> G --> M --> C
  I -.->|new-purchase admission| G
  C -->|fixed execution| X --> B
  B -->|payer to merchant transfer| T
  B -.->|chain observations| V
  V -->|independently verified proof| C
  C -->|verified receipt| M
  M -->|receipt + input| R
  R -->|report + output hash| M
  M -->|report + payment evidence| G --> D --> A
```

The diagram shows logical components, rather than separate operating-system
processes. Hosted commerce checks service identity before admitting a new
purchase to the Agent/MCP path. Core independently validates the buyer's
authority. The watcher runs under Core; its evidence comes from two independent
RPC providers.

The user journey is:

1. The user logs in with a wallet challenge. The browser receives session
   cookies; the user does not share a private key, seed phrase, or signing
   service credential.
2. The user chooses an announced browser wallet, explicitly selects an account
   and a configured network, signs an EIP-712 budget grant, and sets a finite
   token allowance. The page checks the wallet's current chain before signing
   or sending anything; it does not add or switch networks automatically. The
   typed grant is signed data forwarded through Core; no separate budget-bind
   transaction is required.
3. Codex requests device binding. The local bridge opens a temporary account
   link. After the logged-in wallet claims that request, the device presents a
   fresh signed proof and the wallet approves that exact device in Core. The
   local bridge obtains short-lived credentials automatically; the user never
   copies a token. Codex can then discover services and receive a frozen quote.
4. Before a new purchase is admitted, hosted commerce checks the service
   identity and receiving wallet; Marketplace then requests Core's identity,
   policy/risk, budget reservation, and execution checks.
5. An isolated execution path submits the fixed payment. The BudgetExecutor
   contract enforces the signed limits, expiry, revocation, and purchase replay
   protection. A gas relayer pays testnet gas; the user-funded asset moves from
   payer to merchant.
6. Two independently operated RPC paths verify the exact calldata, receipt,
   canonical finality, and events. Core issues the receipt, the HTTP merchant
   accepts it and produces the report, and Marketplace returns the result. If
   delivery fails after payment, recovery retries delivery for the same paid
   order and never creates a second charge.

The initial account link is only a routing intent, not permission to spend.
Core binds device consent to the authenticated wallet and grant. Each MCP call
revalidates the credential against Core, and the server injects the device
identity into previews, execution, and recovery. Devices cannot choose another
owner or execute another device's preview. Wallets have separate Core and
Marketplace state; a shared relayer admission gate serializes testnet payments.

Network addresses, asset decimals, and authorization terms come from the
configured deployment. The browser supports choosing among configured rails;
the current deployment enables one testnet rail and requires a plain EOA wallet.
Choosing an arbitrary mainnet in a wallet does not make that network supported
by the backend.

The current hosted demo issues one finite grant per wallet. The wallet page
cannot yet renew an expired or revoked grant, and the test-asset faucet permits
one claim per address. Repeating onboarding with an existing wallet requires
grant-lifecycle support; deleting its state is not a supported reset.

## Component boundaries

| Component | Responsibility | Must not decide |
| --- | --- | --- |
| Core | Identity, grant, policy/risk, funding, reservation, audit, and payment receipt | Service catalog or arbitrary merchant selection |
| Marketplace | Discovery, quote freeze, order idempotency, delivery, and recovery | Its own budget, payee, or authorization |
| Agent Gateway | The single MCP surface through which an Agent requests commerce | Direct Core, wallet, or merchant access |
| Service identity guard | ERC-8004 owner, wallet, URI, and code-pin checks | Buyer budget or policy approval |
| Isolated signer / relayer | Signs and forwards a fixed, Core-authorized execution; pays test gas | Quote, payee, amount, or policy changes |
| BudgetExecutor | Applies the user-signed EIP-712 hard limits on chain | Core identity or merchant delivery |
| RPC watcher | Independently verifies chain facts and finality | A payment without matching evidence |
| HTTP merchant | Accepts a valid Core receipt and returns the service result plus output hash | A new payment or a new budget |

ERC-8004 identifies the service: its owner, receiving wallet, metadata URI, and
runtime code pins are checked before a purchase. That identity check does not
grant the buyer a budget and does not replace Core policy or risk approval.
ERC-8004 support covers Identity and Reputation. The underlying feedback flow
can prepare feedback after a verified delivery; the original feedback transaction is
verified through both RPC paths before the application exposes the feedback
document. The public document contains the buyer address, score, payment
transaction hash, and result output hash; it excludes the CSV payload. This flow
has local verification and is not part of the wallet binding page; no live
feedback acceptance is claimed. ERC-8004
Validation and x402 are outside the implemented scope.

The main integrations are MCP for the Agent boundary, EIP-191 for wallet login,
EIP-712 for grants and executions, ERC-8004 for service identity, and isolated
KMS-backed signing for the execution path. The signer is an execution component;
Core remains the decision authority.

## Evidence status

The following distinction keeps recorded deployment evidence separate from
future public acceptance:

| Evidence | Status |
| --- | --- |
| Local behavior | Tested implementations cover budget limits, expiry, revocation, replay protection, paid-order recovery, execution isolation, and feedback verification. |
| External Agent integration | Local checks cover device proof, browser consent, authenticated MCP transport, effective consent expiry, device-scoped execution, and wallet selection. On 2026-10-10 the external flow was deployed publicly; the configured Codex stdio bridge initialized and queried its unpaired device state against the live service. Wallet consent and a live purchase remain pending. |
| Monad testnet deployment | Recorded on 2026-10-08: two chain-10143 contracts and the ERC-8004 registration for Agent 2073 were deployed; two independently operated RPC paths checked the deployment, and the HTTPS registration metadata was deployed and verified. |
| Public rollout | On 2026-10-10, release `dd3e8e8` was deployed with existing state retained. Public binding assets and registration metadata returned HTTPS 200, anonymous account access returned 401, and the isolated signer passed role/address validation and both KMS DryRuns. The unavailable secondary RPC was replaced with Ankr; both providers agreed on the finalized boundary and deployed contract hashes. No purchase was signed or broadcast during these checks. |
| Fresh public own-wallet acceptance | Pending: purchase, delivery, same-order replay, revoke, and second-wallet isolation still require a new live acceptance run. |

These records do not claim a complete live closed loop, production adoption, or
a security audit. TestUSD has no monetary value.

## Connect Codex

Install the Python dependencies as described below. Add a separate stdio server
to the Codex MCP configuration, replacing the absolute paths with your local
checkout, interpreter, and a private device-state path:

```toml
[mcp_servers.agentonomy_commerce]
command = "/usr/bin/env"
args = ["-i", "PATH=/usr/bin:/bin", "LANG=en_US.UTF-8", "PYTHONPATH=/absolute/path/agentonomy-commerce", "/absolute/path/agentonomy-commerce/.venv/bin/python", "-m", "examples.monad_commerce.opc_bridge", "--origin", "https://review.agentonomy.xyz", "--state", "/absolute/private/path/agentonomy-commerce/device.json", "--label", "My Codex"]
cwd = "/absolute/path/agentonomy-commerce"
startup_timeout_sec = 30
tool_timeout_sec = 240
```

These stdio configuration fields are documented in the
[Codex MCP guide](https://developers.openai.com/codex/mcp). Keep the private
device state outside the repository. It contains a local device proof key,
never a wallet key or AWS credential. Access credentials stay in bridge memory.
The clean environment prevents the bridge from inheriting operator AWS
credentials. Reload the Codex MCP connection after saving the configuration.

Once wallet consent is complete, the demonstration is:

1. Ask Codex to connect the Agentonomy Commerce wallet. Complete wallet,
   budget, finite allowance, and device consent in the opened account page.
2. Back in Codex, check connection and account readiness, then discover the CSV
   reconciliation service and request a preview with a stable idempotency key.
3. Execute that preview. If payment or delivery is pending, query or recover
   the original purchase ID until its verified result is available.
4. Execute the same `preview_id` again to show the original result and transaction hash,
   then revoke authorization from the wallet page and show that a new purchase
   is denied.

Only the wallet owner signs login, authorization, allowance, and revocation.
The bridge has no AWS access; the isolated server signer receives only a fixed
Core-authorized execution.
The server signer requires a valid short-lived role session. An operator must
renew that session before it expires; an expired session blocks execution.

Recording guides: [technical demo, up to three minutes](docs/video-technical-demo.zh-CN.md)
and [product pitch, up to two minutes](docs/video-pitch.zh-CN.md). Use the same
verified run for both videos and capture all purchase material before revoking
the finite grant; the current demo cannot renew that grant for another run.

## Run locally

Use Python 3.12 for the reproducible local simulation:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps -e apps/node
make PYTHON=.venv/bin/python test-commerce
make PYTHON=.venv/bin/python demo
```

The demo uses simulated settlement. It does not spend real funds or use the
Monad deployment. It selects a local analysis service for a JSON order list.
Its expected behavior is a **0.30** quote, a delivered order-analysis report,
and a replay of the same order without a second charge; the final budget shows
**0.30 used** and **0.00 reserved**. The CSV wallet flow is a separate testnet
composition.

Run the focused verification targets with the same interpreter:

```sh
make PYTHON=.venv/bin/python test-commerce
make PYTHON=.venv/bin/python test-review
make PYTHON=.venv/bin/python test-contracts
make PYTHON=.venv/bin/python test-monad
```

`test-contracts` and `test-monad` require Foundry. The tests exercise useful
behaviors such as quote and purchase idempotency, receipt validation, delivery
recovery, contract limits, revocation, replay protection, and independent
payment verification.

## Where to look

- [`apps/core`](apps/core): identity, grants, policy/risk, reservations, funding, and audit.
- [`apps/marketplace`](apps/marketplace): discovery, quotes, orders, and delivery.
- [`apps/node`](apps/node): the Agent-facing MCP Gateway.
- [`contracts/src/AgentonomyBudgetExecutor.sol`](contracts/src/AgentonomyBudgetExecutor.sol): EIP-712 budget enforcement and token transfer.
- [`examples/monad_commerce`](examples/monad_commerce): wallet-authorized testnet composition.
- [`tests/commerce`](tests/commerce) and [`tests/monad`](tests/monad): focused behavioral and integration checks.

Existing source-file license notices remain applicable. This README grants no
additional repository-wide license.
