# Temporary role integration acceptance

Run date: 2026-10-07. Base `59bbeeb`, branch `codex/monad-role-acceptance`.

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

- Final verification at `59bbeeb`: `make test-monad test-commerce test-review test-submission`
  exited 0. Reported counts were Monad **287**; Core budget,
  recovery and policy migration **21**; Commerce **25**; persistent review and
  recovery **76**; submission packaging **6**. This includes the isolated
  signer,
  KMS primitives/scope, external-wallet loop, Core/API/MCP/UI, canary and the
  bounded deployment operator. The local Anvil smoke deployed both real
  contracts and replayed the journal without signing again. Its two logical
  RPCs use one disposable local node, not two independent public providers.
- At `b160618`, the targeted TDD sequence went from **5 expected failures** to
  **12 passed** after the read-only RPC retry fix. Read-only RPC observation
  retries are bounded to three attempts; a saved deployment transaction is
  broadcast at most once and is reconciled by its original hash.
- Timeout-layer fix commit `4db0ff5` was independently reviewed **APPROVE**;
  recovery/input-retention fix commit `59bbeeb` is included in the final
  verification. Public request ceilings are `Core 180s < Marketplace 240s <
  HTTP 270s`; local and simulation remain `45s`. These are per-layer ceilings,
  so serial Core calls can still exceed an outer deadline; an unknown outcome
  remains inspect/recover-only for the original order.
- `forge build` passed. Deprecation and existing timestamp lint
  warnings were emitted; no verification gate failed.
- Independent RPC-retry review: **APPROVE**. Its focused result was **22 tests /
  125 mock checks**; these are review evidence and are not added to the full
  Monad count.
- The UI change at `32f0211` was independently reviewed **APPROVE**, with
  **8 UI tests passed**. Main-agent focused UI/API/submission verification
  reported **21 passed** and two dependency warnings. A main-agent real browser
  reload, original-order query, and post-`refreshStatus` check confirmed API
  order state `delivered`, the unchanged report, the Monad `10143` payment
  verification summary with the correct payment tx link rather than the
  approval tx, and empty JavaScript errors.

No live Monad acceptance is inferred from these local tests.

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
Configuration was generated from the verified deployment records. The operator
UI previously started and completed owner authorization. The local live API is
running at `http://127.0.0.1:8091/` after restart. The original paid order's
recovery and same-order query replay are recorded below; no replacement payment
was submitted.

## Live wallet, purchase and recovery checkpoint

The OKX onboarding sequence is complete for owner
`0x59899831691aa79507818961773497c751bffc8b` on chain `10143`: wallet identity,
Core mandate, EIP-712 budget grant and finite `1.00 TestUSD` approval all passed.
The approval transaction is [`0xed3026bccfca3d9edebd9bde03aa674cf102c6f41dae03125e8c1e8812c5dbeb`](https://testnet.monadexplorer.com/tx/0xed3026bccfca3d9edebd9bde03aa674cf102c6f41dae03125e8c1e8812c5dbeb),
block `68981135`.

The first live MCP attempt completed search, details and preview and initiated
the execute request for the CSV service, submitting one `0.30 TestUSD` payment:

| Item | Evidence |
| --- | --- |
| Preview / purchase | `preview_739a74c933eb` / `purchase_739a74c933eb` |
| Core reservation | `reserve_c154873b164f` |
| Payment | [`0x2711ae051fbf132e544e984c11db6b3b8b518caa40a70d380520743159408037`](https://testnet.monadexplorer.com/tx/0x2711ae051fbf132e544e984c11db6b3b8b518caa40a70d380520743159408037) |
| Receipt | status `1`, block `68984424`, relayer transaction nonce `2` |
| Independent settlement | Core settled; budget watcher `verified=true`, `two_rpc_verified=true`, finality block `68984424` |
| Initial outer Marketplace state | 45-second timeout before the Core completion response; `payment_submitted` / `RECONCILIATION_REQUIRED` |

The original order was recovered after restart. API recovery and same-order
query replay returned the same CSV input hash and identical delivered object.
The recovery fix preserves paid-order input for 24 hours and restores it for
this controlled same-hash recovery. The API order state is `delivered`.
`delivered_and_replayed` is the acceptance evidence state for this same-order
recovery/query replay, not an API order enum. Delivery count changed from `0`
to `1`, and the query replay remained at `1`. `same_order_same_payment=true`;
no replacement payment was submitted. This evidence does not claim a second MCP
execute.

| Item | Evidence |
| --- | --- |
| Recovery evidence state | `delivered_and_replayed` (API order state: `delivered`) |
| Order / preview | `purchase_739a74c933eb` / `preview_739a74c933eb` |
| Input / output hash | `0x328cd8c29321f7cccfe7b971af18c62f601ece87b30730bf2c084563d2fa39b9` / `0x12f2f322a22b05deb897b33c0d5c7a1e10060ad79ef70b9967b526e30bdd89be` |
| Payment proof | Same `0x2711ae051fbf132e544e984c11db6b3b8b518caa40a70d380520743159408037`, chain `10143`, status `1`, block `68984424`, verified through both RPCs |
| Budget after replay | used `0.30`, reserved `0.00`, remaining `0.70` TestUSD |
| Delivery count | `0 → 1`; same-order query replay remains `1` |
| Relayer pending nonce | `3` (`2` deployments + `1` purchase), not a payment count |
| Report | 2 input rows; income `10.00`, expenses `2.00`, net `8.00` USD |

The returned `service_result` carries `settlement_mode=local_anvil` as a
historical merchant label preserved in the original hashed report. It does not change the verified live
payment proof on chain `10143`. The public review site still uses simulated
settlement.

The live purchase and recovery loop is complete for this original order.
Main-agent browser acceptance at `32f0211` passed: the UI
query and `refreshStatus` path confirmed API order state `delivered`, the
unchanged report, budget `0.30/0.70`, pending nonce `3`, delivery count `1`,
the Monad `10143` payment verification summary, and the correct payment tx link
rather than the approval tx. JavaScript errors were empty, and the objective
legacy merchant-label prompt is recorded. Core-plus-chain revocation still
requires the user's OKX action and must wait until successful
purchase/recovery/query footage is recorded. Before recording, the
remaining `0.70` TestUSD permits at most one additional `0.30` purchase while
leaving at least `0.30` for the post-revocation refusal comparison. Recording
and submission remain pending.

See [the recording sequence](recording-checklist.md) for the remaining revoke,
recording and submission steps.
