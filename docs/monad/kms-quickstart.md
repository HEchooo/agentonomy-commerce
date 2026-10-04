# External OKX wallet and dedicated KMS acceptance

This is an operator-run **Monad testnet** path. The public wallet supplies its
own signatures; the application never receives its private key. Public chain
acceptance remains pending: a passing local test is not a Monad transaction.
The existing review website is a separate deployment and is not upgraded by
starting this command.

## Scope and prerequisites

- Buyer: `0x59899831691aa79507818961773497c751bffc8b`, OKX, chain `10143`.
- Execution signer: `0x968DbAbb8Dca19C4A8174A260Cc40Db66BdB7915`.
- Gas relayer: `0xBdCb39Ac5Ff83485cb35160F0DdAA0b7446Ee009`.
- Merchant payee uses the relayer address for this no-value test asset.
- Total budget and finite allowance: **1.00 TestUSD**; per purchase cap:
  **0.50 TestUSD**; CSV service price: **0.30 TestUSD**; signed grant: one day.
- Buyer needs test MON for approval and revocation; relayer needs test MON for
  deployment and payment execution. The execution-signing address needs no gas.
- An administrator must apply the narrow, exact-two-key permission described in
  [the administrator handoff](kms-administrator-handoff.md). A local approval
  cannot substitute for AWS permission. Existing PROD keys remain separate.

## Verify and prepare

Use Python 3.12 and the locked environment from [deployment.md](deployment.md).
Foundry must be on `PATH` for local chain tests.

```sh
forge build --root contracts
make PYTHON=.venv/bin/python test-monad
```

The read-only deployment planner prints two concrete legacy CREATE transactions:
fixed-supply TestUSD minted to the buyer, then the budget executor. It includes
artifact hashes, predicted addresses, nonce and maximum gas cost. Replace the
example nonce and gas price with fresh, independently checked values:

```sh
.venv/bin/python -m scripts.monad.deployment_plan \
  --nonce 0 --gas-price-wei 200000000000 \
  --payee 0xBdCb39Ac5Ff83485cb35160F0DdAA0b7446Ee009 --rpc-check
```

This command cannot sign or broadcast. A blocked balance/nonce/gas report is not
deployment approval. The purchase signing worker deliberately cannot sign
contract creation; deployment requires a separate, reviewed operator step for
these exact two transactions. Do not loosen the purchase scope to deploy.

After deployment, prepare a private `configuration.json` outside version control:

1. `deployment`: `mode`, `chain_id`, two distinct HTTPS `rpc_urls`, `token`,
   `executor`, `payee`, `token_decimals`, `gas_limit`, `max_gas_price_wei`, `owner`,
   `execution_signer`, `relayer`, `domain`, `token_code_hash`, `executor_code_hash`.
   Use the actual deployed addresses/code hashes, not predicted values alone.
2. `signer_configuration`: named AWS `profile`, `region` (`ap-southeast-1`),
   `account_id`, exact `execution_key_arn` and `gas_key_arn`, plus `scope`.
3. `scope`: `network` (the same network fields as deployment, excluding wallet,
   domain and code-hash fields), `owner`, `execution_address`, `relayer_address`,
   `nonce_min`, `nonce_max`. After exactly two initial CREATEs from nonce 0,
   a three-purchase canary uses nonce 2 through 4; always check current nonce.

No access key, secret, session token, wallet key or seed belongs in this JSON.
Configuration must match the signed budget and deployed contracts exactly.
Startup independently checks both RPCs, token identity, executor chain/token,
canonical Verified boundary and actual runtime code hashes.

## Run the operator page

```sh
PYTHONPATH=.:apps/core:apps/node .venv/bin/python \
  -m examples.monad_commerce.public_api \
  --config /absolute/private/configuration.json \
  --state-dir /absolute/private/monad-state --port 8091
```

Open `http://127.0.0.1:8091` in an OKX-enabled browser. It uses a same-origin,
HttpOnly session cookie; no manual backend token is required. It intentionally
binds only loopback. Do not expose this operator interface through a public
proxy. A server deployment additionally needs separate service identities and
protected installation of short-lived AWS sessions: process separation alone
is not an operating-system security boundary.

The explicit wallet steps are identity challenge, Core mandate, EIP-712 budget
grant, and finite token approval. Each step pins the wallet and chain. The
application cannot perform these wallet confirmations on the buyer's behalf.
Only the dedicated signer subprocess loads AWS configuration. Core, Marketplace,
Watcher and `clink_node` receive structured capabilities, never AWS credentials.

After setup, preview the CSV service and execute the same preview. Preserve its
purchase ID. The recovery button calls the saved purchase's own preview;
`payment_submitted` checks the original payment and `paid_but_undelivered`
retries delivery without creating another payment. A finalized wrong/reverted
approval hash can be corrected explicitly. An unknown or disagreeing hash stays
pinned for investigation; it must not trigger automatic resubmission.

Revoke first disables Core funding, then the buyer signs the original grant's
onchain revocation. Refresh restores the recorded revocation and transaction
hash. Core revocation alone does not prove onchain revocation. Previous paid
orders remain readable/recoverable after revocation.

## Acceptance evidence

Retain public transaction hashes, contract addresses/code hashes, both RPC
observations, finality boundary, exact TestUSD movement and delivered result.
Verify replay/restart leaves one payment; verify revoked grants reject new
funding. Do not publish signed grants, raw signed transactions, protected state,
receipt secrets or AWS material. Local tests use ephemeral fixture keys and fake
KMS DER responses; they do not prove live AWS IAM permission or public funding.

With the operator API running and wallet setup complete, the unified MCP entry is:

```sh
PYTHONPATH=.:apps/core:apps/node .venv/bin/python \
  -m examples.monad_commerce.public_node --origin http://127.0.0.1:8091
```

This stdio process exposes the existing service search, preview, execution and
purchase-status tools through `clink_node`; it cannot issue owner signatures or
arbitrary KMS requests. Agents keep the original preview/purchase references when
resuming an order. Final local verification is recorded in [kms-acceptance.md](kms-acceptance.md).
