# Public wallet commerce implementation

Accepted goal: a reviewer opens the HTTPS product, logs in with their own wallet,
claims valueless TestUSD, grants a finite budget, binds the hosted demonstration
Agent, and obtains a service result backed by a verified Monad testnet payment.
No wallet private keys or manually copied runtime tokens are required.

The copied Core remains the sole authority for identity, signed grants, policy,
budget, funding and audit. Marketplace keeps preview, order idempotency and paid
delivery recovery. New modules are composition adapters, not a second payment
implementation. The working original buyer and deployed contracts remain intact.

## Sequence and acceptance

1. Add a fixed-supply, testnet-only claim token. Validate finite per-address claims,
   conservation and the absence of issuer/admin methods. Local contract tests only.
2. Add fresh browser wallet proof and canonical OPC installation consent to the
   existing external-wallet Core. Bind challenges to browser/CSRF digests; repeat
   login must preserve the original grant, counters, allowance and orders.
3. Compose isolated Core/Marketplace state and one hosted device installation per
   wallet. Route by server-owned opaque tenant identifiers. Check browser authority
   on every website request and OPC authority before every Agent operation.
4. Put **all tenants** behind one durable shared relayer gate. Core owns order
   replay protection. Retain unknown payment outcomes across restarts; clear the
   lane only from canonical Core settlement evidence. Never infer success from a
   pending nonce or accept a browser-supplied completion proof.
5. Add the HTTPS API and wallet UI: login, claim, finite grant/approve, OPC consent,
   preview, execute, query/recovery, revoke and logout. Rotate short OPC credentials
   inside the hosted Agent; no bearer token in browser responses or localStorage.
6. Verify cross-wallet access denial, fresh login, consent expiry/revocation,
   purchase idempotency, crash recovery and finite wallet transactions. Run the
   relevant copied Core/Node/Commerce/Monad and contract regressions.
7. Produce a concrete two-CREATE deployment plan for the new faucet and executor.
   Check role, key pins, nonce, gas and code hashes read-only. Obtain final approval
   for the concrete testnet deployment and public runtime scope before broadcasting.
8. Deploy to the independent review machine with fresh protected state and role
   sessions through the existing credential boundary. Complete two-wallet live
   acceptance, update public evidence and materials, then hand over wallet actions
   and video recording. Review the hackathon form before final submission.

## Limits and handoffs

- Public anonymous admission is bounded; connecting an address is not login.
- Each wallet's existing one-day grant is preserved, never silently renewed.
- TestUSD has no value. Users supply testnet MON for their own wallet transactions.
- KMS execution and gas keys stay in the isolated signer worker. No AWS credentials
  enter Node, Core, Watcher, browser responses, source archives or logs.
- No Clink DEV/PROD service, key policy, IAM policy, mainnet or payment switch changes.
- New live deployment, role renewal if expired, wallet signatures and final videos
  remain explicit handoffs after the reviewed code and concrete plan are ready.

## Progress

- Faucet: implemented; 10 targeted / 64 full contract tests reported passing.
- Relayer gate: implemented; 35 targeted tests passed, including durable recovery.
- Browser/OPC Core: implemented; fresh login, grant retention and expiry/refusal tested.
- Public API, hosted composition, MCP SDK and frontend: implemented; relevant local regressions passed.
- Protected Linux signer, role-session installer, service unit and deployment plan prepared.
- Actual KMS role/key pins and DryRun passed; no actual signature or transaction generated.
- GCP OS Login is blocked; public rollout and two-wallet live acceptance remain pending.
- Final local gate: Monad479, Core budget21, contracts64, Commerce25, Review76,
  submission6, canonical OPC15 and Node MCP10 passed. A final focused frontend,
  hosted security and server run passed27 after order-reference, fallback and
  session-cookie concurrency changes. The independent final UI review found no
  blocking issue;11 frontend tests passed.
