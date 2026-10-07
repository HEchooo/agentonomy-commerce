# Temporary role integration acceptance

Run date: 2026-10-07. Base `2d34833`, branch `codex/monad-role-acceptance`.

## Live AWS evidence

An independent temporary session was assumed through the approved test runtime
role. STS returned the exact expected assumed-role identity and account. The
session policy narrows access to the execution and Gas test keys. Both enabled
secp256k1 public keys matched their pinned addresses and both signing DryRuns
returned `DryRunOperationException`. DryRuns for the response key and all three
excluded production keys returned `AccessDeniedException`.

The actual `KmsSignerBridge` subprocess then started from the protected temporary
session and returned `ready`. Startup performed identity, key metadata, public
key and DryRun checks. Neither the bootstrap nor this health check requested an
actual signature or broadcast a transaction. Clink services, IAM and key policies
were not changed by this work.

Credential contents are stored only in private operator session files outside
the repository. The runtime receives file/profile references, with no operator
profile fallback. Ordinary Node, Marketplace, Core and Watcher processes do not
load the credentials. This local process boundary is not a same-user OS sandbox.

The session expires at **2026-10-08 02:32 China time**. This proves the current
temporary session, not unattended renewal or indefinite signing availability.

## Verified regressions

- Complete Monad suite: **270 passed**, including the isolated signer,
  KMS primitives/scope, external-wallet loop, Core/API/MCP/UI, canary and the
  bounded deployment operator. The local Anvil smoke deployed both real
  contracts and replayed the journal without signing again. Its two logical
  RPCs use one disposable local node, not two independent public providers.
- Core budget, recovery and policy migration regressions: **21 passed**.
- Existing Commerce demo: **16 passed**; persistent review and recovery:
  **76 passed**; submission packaging: **6 passed**.
- Current compiled Solidity suite: `forge test --root contracts -q` exited 0.
- Independent role integration review approved the final change after a
  special-file startup blocking issue was fixed. The FIFO regression and
  focused signer/KMS tests are included in the broader Monad count above.
- Independent deployment review approved both specification and quality after
  recovery fixes. Its final targeted recheck passed seven tests covering the
  four reported gaps; these overlap the full Monad suite.

Framework deprecation warnings and Foundry invariant-target discovery warnings
were emitted. No live Monad acceptance is inferred from these local tests.

## Public Monad status

Both configured RPCs reported chain `10143`; buyer balance was **40 test MON**
and Gas relayer balance **0**. The relayer pending nonce remained zero. The
concrete plan contains two CREATEs at a combined maximum cost of **0.8 test MON**
(2,000,000 gas each at 200 gwei), plus separate later purchase/wallet gas.

No public contract has been deployed in this run. Predicted addresses are not
deployment evidence. Funding, live deployment, OKX wallet signatures, finite
allowance, actual purchase/delivery and public replay/revocation proof remain
pending. The existing public review site still uses simulated settlement.

See [the recording sequence](recording-checklist.md) once live acceptance passes.
