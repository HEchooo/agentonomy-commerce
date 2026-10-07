# Own-wallet public Monad product readiness

Status on 2026-10-08 (Asia/Shanghai): the new hosted composition is implemented
and its relevant local regressions passed. It is **not yet deployed or accepted on the
public HTTPS endpoint**. `review.agentonomy.xyz` still serves the earlier
simulated review product. The previously accepted real single-wallet order and
contracts remain unchanged; see [role-session acceptance](role-session-acceptance.md).

## Implemented flow

1. Connect an EOA browser wallet on Monad testnet 10143. Sign a fresh wallet
   challenge bound to the HTTPS domain and the browser/CSRF session.
2. Claim 1.00 valueless TestUSD from the new fixed-supply claim contract.
   Users supply test MON for their own claim, approve and revoke transactions.
3. Authorize the existing Core business grant, sign its EIP-712 chain budget,
   and approve exactly 1.00 TestUSD. The grant lasts one day, has a 1.00 total
   limit and a 0.50 per-payment limit. Login never renews or resets it.
4. Sign installation consent for this wallet's hosted demonstration Agent.
   Canonical OPC exchanges a short-lived credential inside the hosted process;
   the user does not copy a token. Each MCP admission and callback rechecks Core.
5. Discover and preview the CSV reconciliation service through the shipped
   Node and actual MCP SDK, then purchase for 0.30 TestUSD. Core retains identity,
   policy, reservation, funding and audit authority. Marketplace retains order
   idempotency, verified payment and paid-delivery recovery.
6. Query/recover the same order, or revoke Core and chain authorization with
   distinct states. A delivery failure does not authorize another payment.

This release implements the website-hosted Agent flow. An externally installed
Agent's publicly reachable pairing/MCP endpoint is not part of this rollout.

## Isolation and recovery

- Wallet proof determines the server-owned opaque tenant; clients cannot select
  a user, tenant, Agent or merchant. Each wallet has separate Core/Marketplace
  state and its own hosted installation.
- Browser credentials are Secure HttpOnly cookies. State stores browser/CSRF
  digests; no AWS credential or Agent token appears in a browser response.
- One durable relayer gate spans all wallets. Unknown transaction outcomes
  retain their original order across restarts. Only canonical Core evidence
  verified by both RPCs can release a submitted payment's lane.
- Re-login preserves the original identity, grant, counters, allowance and
  orders. An unsigned grant challenge may move to a newly verified browser;
  signed or expired authorization is never silently reconstructed.
- The browser preserves only the current wallet's original preview, purchase
  and idempotency references. It restores them only after fresh server
  authentication; a refresh or timed-out response never automatically executes,
  signs or broadcasts a replacement payment.
- Linux uses `agentonomy-web` and `agentonomy-sign`. The root-installed launcher
  accepts only a canonical owner change from its pinned configuration. Keys,
  role, network, credential path, nonce window and gas limits remain exact.
  The release and virtual environment must be root-owned and immutable to both
  service accounts. Local process isolation alone is not an OS security boundary.

## Bounded review deployment

The application admits at most 128 active browser sessions, 32 persistent wallet
namespaces and four open wallet runtimes. This is a supervised hackathon trial,
not an unlimited multiuser production service. The claim pool contains exactly
1000 TestUSD, with one 1.00 claim per address and no mint/admin/upgrade capability.
The application admission limit is separate from the contract's claim pool.

The concrete deployment proposal is in
[public deployment plan](public-deployment-plan-20261008.md). Its public relayer
nonce window permits nine purchase transactions; extending that window requires
a separately reviewed scope update. Failed or exhausted authorization does not
create a replacement grant or wallet budget.

## Remaining live gates

Local verification: 479 Monad tests, 21 Core budget/recovery/migration tests,
64 contract tests, 25 Commerce tests, 76 Review tests, six submission tests,
15 canonical OPC tests and ten Node MCP gateway tests passed. These include
isolated two-owner composition and failure/recovery fixtures; they do not
replace two-wallet acceptance on the actual HTTPS/Linux host.

- GCP currently refuses OS Login for the existing operator account because the
  target organization requires `roles/compute.osLoginExternalUser`. No account
  switch, SSH key addition, instance metadata workaround or server change was
  performed. Administrator access is required before the protected rollout.
- Review the concrete two-CREATE plan and public relayer scope. Recheck the
  pending nonce, gas price, balance, role, key pins and both RPCs before execution.
- Install the protected role session through SSH/stdin using the standalone
  guarded installer. Verify the two OS identities and fixed launcher on the
  actual host, not merely from local source inspection.
- Verify both new contract receipts and code hashes, produce the deployment
  manifest, then point HTTPS to the new loopback service with fresh protected
  state. A prepared configuration with missing code hashes must remain blocked.
- Complete live own-wallet login, claim, consent, approve, purchase, delivery,
  same-order recovery and revoke/refusal. Verify a second wallet cannot access
  the first wallet's orders or budget. Only then update public evidence, the
  saved submission form and final recording materials.

No Clink DEV/PROD service, IAM policy, Key Policy, mainnet authorization or
production payment switch is part of this change.
