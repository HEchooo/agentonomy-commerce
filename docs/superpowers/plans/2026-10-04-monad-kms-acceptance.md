# Monad KMS acceptance implementation

Base: `335d47f`; isolated branch `codex/monad-kms`.

The existing local Anvil loop is complete. This continuation adds external wallet
onboarding and a dedicated, restricted KMS signing process for public testnet.
Development and local verification are authorized. Public acceptance is only
complete after actual Monad receipts, dual RPC verification and HTTP delivery.

## Fixed scope

- Buyer: `0x59899831691aa79507818961773497c751bffc8b`, OKX wallet.
- Monad testnet chain 10143. Two independent HTTPS RPCs, Verified boundary.
- Fixed-supply TestUSD (6 decimals, no monetary value), one executor and merchant.
- Two new Hackathon KMS keys only, one execution and one gas role.
- No production changes; no long-term credentials exported to servers.
- Core retains identity, signed business grants, policy/risk, reservation and audit.
- The signer process alone loads AWS credentials. Node/Marketplace/Watcher/Core
  receive no AWS credentials. No generic signing endpoint exposed to browsers.
- Wallet supplies identity signature, business grant signature, EIP712 grant and
  finite token approval. Never generate or retain a public buyer private key.
- Durable transaction preparation precedes broadcast. Unknown means reconcile
  the existing attempt; paid delivery recovery never initiates another payment.
- Default deployment command is read-only planning. Signing/broadcast require a
  concrete bounded plan. No public blockchain calls from tests by default.

## Tasks

- [x] 1. New standalone secp256k1 KMS adapter, strict EIP155 legacy serialization,
      malformed/provider error and recovery tests; no live AWS calls in tests.
- [x] 2. Read-only two-transaction deployment planner: bytecode constructors,
      predicted addresses, nonce, hashes, exact buyer supply and gas bounds;
      dry-run tests and administrator policy handoff.
- [x] 3. Restricted signing worker and pipe client; grant/execution/transaction
      scope validation and credential environment isolation; adversarial tests.
- [x] 4. Public Core external-wallet onboarding composition and durable state;
      browser wallet setup with chain/address pins, actual grant signatures,
      allowance validation, no injected buyer secrets.
- [x] 5. Public Marketplace composition, original order recovery and result flow;
      local tests exercise external-wallet + controlled signer composition.
- [x] 6. Independent review and complete relevant regression. Merge verified
      code into Commerce, record remaining public acceptance prerequisites.
- [ ] 7. After AWS administrator permission and gas funding: KMS selfchecks,
      present concrete deployment/payment scope, wallet signing, Monad deployment,
      canary, replay, revoke and deliver evidence. Stop at required human action.

## External readiness, verified 2026-10-04

The two new KMS keys exist and public addresses are derived. `kms:Sign` is denied.
Applying the user-approved, exact-two-key IAM policy was rejected by AWS because
the operator lacks `iam:PutUserPolicy`. No IAM policy was applied. Administrator
cooperation is necessary; human approval does not confer AWS permissions.

Both buyer and gas relayer read balance zero and pending nonce zero on both Monad
RPCs. The official faucet remains at its verification step; no claim completed.
A read-only two-CREATE plan uses a combined 0.8 test MON maximum deployment cost
at 200 gwei and 2,000,000 gas per transaction; it is blocked by insufficient
funding. Recheck all prices/nonces/balances before use. No public deployment or
payment claimed. Operational steps: [KMS quickstart](../../monad/kms-quickstart.md).
