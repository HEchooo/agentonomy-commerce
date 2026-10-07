# Temporary role integration acceptance

Run date: 2026-10-07. Base `b160618`, branch `codex/monad-role-acceptance`.

## Live AWS evidence

An independent temporary session was assumed through the approved test runtime
role. STS returned the exact expected assumed-role identity and account. The
session policy narrows access to the execution and Gas test keys. Both enabled
secp256k1 public keys matched their pinned addresses and both signing DryRuns
returned `DryRunOperationException`. DryRuns for the response key and all three
excluded production keys returned `AccessDeniedException`.

The actual `KmsSignerBridge` subprocess then started from the protected temporary
session and returned `ready`. Startup performed identity, key metadata, public
key and DryRun checks. This was the pre-signing validation stage. The separate
bounded deployment operator then used the real Gas signing path for both CREATEs:
each was signed once and sent once, with no replacement. Clink services, IAM
and key policies were not changed by this work.

Credential contents are stored only in private operator session files outside
the repository. The runtime receives file/profile references, with no operator
profile fallback. Ordinary Node, Marketplace, Core and Watcher processes do not
load the credentials. This local process boundary is not a same-user OS sandbox.

The session expires at **2026-10-08 02:32 China time**. This proves the current
temporary session, not unattended renewal or indefinite signing availability.

## Verified regressions

- Complete Monad suite: **282 passed**, including the isolated signer,
  KMS primitives/scope, external-wallet loop, Core/API/MCP/UI, canary and the
  bounded deployment operator. The local Anvil smoke deployed both real
  contracts and replayed the journal without signing again. Its two logical
  RPCs use one disposable local node, not two independent public providers.
- At `b160618`, the targeted TDD sequence went from **5 expected failures** to
  **12 passed** after the read-only RPC retry fix. Read-only RPC observation
  retries are bounded to three attempts; a saved deployment transaction is
  broadcast at most once and is reconciled by its original hash.
- Core budget, recovery and policy migration regressions: **21 passed**.
- Existing Commerce demo: **16 passed**; persistent review and recovery:
  **76 passed**; submission packaging: **6 passed**.
- Current compiled Solidity suite: `forge test --root contracts -q` exited 0.
- Independent RPC-retry review: **APPROVE**. Its focused result was **22 tests /
  125 mock checks**; these are review evidence and are not added to the full
  Monad count.

Framework deprecation warnings and Foundry invariant-target discovery warnings
were emitted. No live Monad acceptance is inferred from these local tests.

## Public Monad status

Both configured RPCs reported chain `10143`. Before the first CREATE, the
deployment funding observation was **24 test MON** and the relayer pending nonce
was **0**; the same values were checked through both RPCs before the independent
temporary role was used. The concrete two-CREATE plan has a combined maximum
cost of **0.8 test MON** (2,000,000 gas each at 200 gwei), plus separate later
purchase and wallet gas.

The first CREATE is now real and independently verified through both RPCs:

| Item | Evidence |
| --- | --- |
| Contract | TestUSD at `0x6f36d16b27713f7b1a7e1d2773420fca9fd494c9` |
| Transaction | [`0x441be12ebb19c739b041898ceb2780e6317103d4dbd20492e9462e638bfa676b`](https://testnet.monadexplorer.com/tx/0x441be12ebb19c739b041898ceb2780e6317103d4dbd20492e9462e638bfa676b) |
| Deployment block | `68930167` |
| Verified boundary | `68930181` |
| Runtime code hash | `0xd1626354560afb23108c77da4259e62641b6f3de8e18df1715e396d78e3d0eda` |
| Deployment attempts | Signed once and sent once; no replacement |
| Initial TestUSD balance | Buyer balance of **1.00 TestUSD** verified at the deployment block |

The second CREATE is now real and independently verified through both RPCs:

| Item | Evidence |
| --- | --- |
| Contract | BudgetExecutor at `0x391b21c017d8938820c5744ac70603857df8970c` |
| Transaction | [`0x5a9dd34eb22039218f9af880d838fc1370487079656533bde05f24fa65c61016`](https://testnet.monadexplorer.com/tx/0x5a9dd34eb22039218f9af880d838fc1370487079656533bde05f24fa65c61016) |
| Deployment block | `68933987` |
| Verified boundary | `68934001` |
| Runtime code hash | `0x79672b62feb37fd13e8b1e923826f3fedabd2181b65a6bdd4167b28499183acf` |
| Deployment attempts | Signed once and sent once; no replacement |

The deployment CLI reports `complete`, `pending=false`, with both records
verified. Both RPCs subsequently reported relayer latest and pending nonce `2`.
The actual runtime configuration was generated from the verified records. The
operator UI started at `http://127.0.0.1:8091/` after its independent deployment
and owner checks. A fresh browser session displayed the expected chain, owner
and contract addresses, phase `wallet`, and no JavaScript errors. This proves
the page is ready for owner setup, not that a purchase has been accepted.

OKX identity, Core mandate, EIP-712 grant, finite allowance, purchase/delivery,
replay/restart and Core-plus-chain revocation remain pending. The existing
public review site still uses simulated settlement.

See [the recording sequence](recording-checklist.md) once live acceptance passes.
