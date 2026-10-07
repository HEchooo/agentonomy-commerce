# Monad budget commerce progress

Plan: docs/superpowers/plans/2026-10-03-monad-budget-commerce.md
Base: 029cd0b. Branch: codex/monad-budget-commerce.
Worktree: /private/tmp/agentonomy-commerce-monad-20261003.

## Constraints
- Standalone copied runtime; no sibling Clink runtime imports.
- Original Oct3 constraint: no public broadcasts before later scope approval. Oct7 approved bounded Monad Testnet deployment/payment supersedes that portion; no Clink production changes, final submission or license grant.
- Root owns architecture/security/integration/final acceptance. Bounded Luna workers own disjoint files.
- New EIP712 protocol frozen in docs/monad/design.md. Changes require coordinated review.

## Tasks
- [x] Isolated worktree and independent Python 3.12 lockfile environment.
- [x] GitNexus index agentonomy-monad-dev.
- [x] Freeze protocol v1.
- [x] Fresh baseline regression evidence.
- [x] Solidity implementation, adversarial tests, invariant tests, dry-run script (54 tests).
- [x] Python codec, shared vectors and strict watcher; finality/revert review fixes.
- [x] Core binding, durable attempts, real local-chain composition and MCP/UI.
- [x] Local complete-loop and failure/recovery tests.
- [x] Independent spec and quality reviews.
- [x] Complete relevant regressions and deployment/material preparation.
- [ ] Full public product acceptance (actual HTTPS multi-wallet product, revoke/refusal and videos).

## Evidence — pre-2026-10-07 historical baseline
At this historical checkpoint no public testnet deployment/transaction was claimed.
The dated Oct7 continuation entries below supersede these pending statements.
- Contracts 54 passed, invariant 128,000 calls.
- Existing Commerce 16, Review 76, submission 6, workspace 12, Node 841 (1 PostgreSQL skip), E2E 2, Hosted 522 (4 PostgreSQL skips), Marketplace 320 and 16 prediction smoke scripts passed.
- Full Core 2,116 passed; final focused budget 21 passed.
- Final Monad gate 71 passed, including preflight and behavioral stale-session tests.
- Actual Anvil payment, HTTP delivery, application restart, paid-delivery recovery, budget exhaustion and RPC disagreement tested.
- Desktop and 390px browser checked; refresh/recovery did not create another payment.
- Core independent verification accepted all five initial findings. Final integration review accepted paid-delivery recovery and identified public redaction, stale browser IDs and preflight boundary checks; those final fixes passed their focused and end-to-end gates.
- Public wallet/signers, network deployment, license choice, canary and final submission remain pending user cooperation.
- GitNexus staged change detection: 72 files, 5 affected execution flows, MEDIUM. Git diff confirms source changes scoped to new rail/composition and policy network migration.
- Implementation commit `87f7538` was fast-forwarded into the original Commerce main checkout. Merged-location smoke passed: 71 Monad tests and 21 Core budget tests. Original untracked plan and verification/review logs are preserved under `.artifacts/monad-implementation/`. No remote push.

## 2026-10-07 continuation
Base 2d34833, branch codex/monad-role-acceptance; isolated worktree /private/tmp/agentonomy-commerce-role-20261007-code.
User authorizes dedicated test role and Monad public acceptance, superseding earlier no-broadcast planning constraint. Clink DEV/PROD changes remain prohibited.
- Live independent role session verified, two test-key DryRun passes; response + three PROD key DryRuns denied. No actual signatures/broadcasts yet.
- Signer identity/session guards: completed and committed as 49e60b8.
- Bounded deployment CLI: implemented with purchase scope unchanged; final recovery re-review APPROVE (specification and quality), targeted7 passed.
- Relayer 0 MON on two RPCs; buyer 40 MON. Requested 2 test MON funding; wallet signatures later required.
- Role integration: task review APPROVE, spec + quality after FIFO nonblocking fix. Focused100 passed; expanded pre-FIFO136 passed; live isolated worker health/DryRun ready (no actual signatures). Reports /private/tmp/commerce-role-{task-report,review}.md.
- Existing Commerce16 / Review76 / submission6 passed; compiled Solidity forge test exited0.
- Deployment review fixes implemented and approved: strict complete-journal cardinality and summary schema; private lock/file validation; receipt/transaction lag and RPC recovery; fresh first-CREATE recheck before second-CREATE progress; historical mint balance proof after later purchases; immediate pending on ambiguous broadcast; durable original transaction evidence after storage failures. Latest full Monad suite270 passed, Core budget/recovery/migration21 passed. Brief /private/tmp/commerce-deployment-task-brief.md; review /private/tmp/commerce-final-review.md.
- Public utility must not hardcode private AWS account/key identifiers; reviewed private signer config pins exact user-supplied role and keys, worker validates STS against that exact role. Private live config prepared in /private/tmp/agentonomy-commerce-deployment-20261007 (no credentials); actual credentials remain only in separate protected role session directory.
- Final staged GitNexus check: seven files, 141 symbols, eight execution flows, HIGH. All affected flows start in the new bounded deployment operator; reviewed risk and no Clink modifications. Diff whitespace check passed; changed tracked files contain no actual private AWS account/key identifiers or private-key PEM blocks. Public deployment remains blocked on relayer funding.

## 2026-10-07 funded live deployment
This entry supersedes the earlier zero-balance and no-public-deployment status.
- Both RPCs confirmed 24 test MON and nonce0. Fresh isolated-role worker identity/key/DryRun checks passed before real signing.
- TestUSD and BudgetExecutor CREATEs both completed and independently verified, each signed and broadcast once. Exact public transaction/address/code-hash evidence is in docs/monad/role-session-acceptance.md. Both RPCs now report latest/pending nonce2.
- Intermittent read failures during live verification were diagnosed at the RPC eth_call boundary. Commit b160618 adds at most three attempts for read-only RPCs, preserving one attempt for broadcasts and all verification checks. TDD,282 Monad,16 Commerce and21 Core budget/recovery/migration passed; independent review APPROVE. GitNexus staged scope two files, zero affected processes, LOW.
- Actual private runtime configuration was generated from complete deployment records. The loopback operator UI at http://127.0.0.1:8091/ is running from the main checkout, state preserved in the existing private run directory. Browser shows expected owner/chain/contracts and wallet phase without JS errors.
- Still pending: user OKX identity/mandate/EIP712/finite1.00TestUSD approval, actual0.30 purchase+delivery, same-order recovery/restart and revocation proof. Public review site remains simulated. Do not claim full public acceptance or record the final demo yet.

## 2026-10-07 delivered purchase and saved submission draft
This entry supersedes earlier wallet/payment-pending statements, which remain historical.
- The OKX owner completed identity, mandate, signed grant and finite 1.00 TestUSD approve. Actual MCP initiated one 0.30 payment; successful receipt block 68984424 verified through both public RPCs/Core watcher.
- Original purchase `purchase_739a74c933eb` recovered after a transport interruption and delivered one CSV report (income10/expense2/net8). API recovery/query replay and MCP purchase/status reads passed; used0.30/reserved0/remaining0.70, deliveries1. Relayer pending nonce3 includes two CREATEs and one purchase, not three payments. A second MCP execute was not performed.
- Commit 4db0ff5 bounds real-rail timeouts; 59bbeeb persists original paid input for recovery; 32f0211 gates verified receipt UI and preserves original order/report. Full related gate: Monad287/Core21/Commerce25/Review76/submission6; final focused UI/API/submission21 passed. No application code changed during the later materials pass.
- Monad login succeeded. Description/access instructions saved at 2026-10-07T15:22Z; exact copy in submission/monad/form-saved.json. Deadline Oct14 11:59 GMT+8, track Trust, Identity & AI Infrastructure, checklist3/5. No final submission.
- Read-only GCP check confirms the independent review VM RUNNING, e2-medium, IP34.21.234.127. No remote change. Actual Monad UI/API/merchant remain loopback; public review still simulated. Required real HTTPS product and two hosted video URLs remain empty.
- Remaining: chosen public access mode and deployment; owner onchain revocation/refusal while balance remains; recording; license/rule review; final user review. Do not revoke current grant or pay another order just for material cleanup.

- Later user steering: reviewers must use their own wallets. Website wallet login → bounded consent → OPC installation/short-lived credential flow is being discussed. Root confirmed the copied OPC service uses 300-second access credentials and 10-minute pairing; the current Monad fixed-owner adapter is not yet that public OPC flow. See docs/monad/public-live-readiness.md.

## 2026-10-08 own-wallet hosted composition

This entry supersedes the earlier discussion-only status of public OPC composition.
Plan: docs/superpowers/plans/2026-10-08-public-wallet-commerce.md.
- Implemented fresh HTTPS wallet proof, finite business/chain grants, fixed-supply
  TestUSD claim, per-owner canonical OPC installation consent and short credential
  exchange inside the hosted Agent. The browser receives no Agent bearer token.
- Actual MCP SDK calls the copied Node/Core/Marketplace implementation. Per-wallet
  state remains isolated, while all wallets share one durable relayer gate.
  Unknown outcomes keep the original order. Re-login/expiry/revocation do not reset
  grants, counters or orders. Frontend retains only nonsecret original order
  references and never automatically executes after refresh.
- Added a protected Linux signer launcher, separate web/sign OS users, a root
  release/service boundary and a standalone temporary-session installer. Exact AWS
  identity/key pins live in private configuration, not source or browser state.
- Independent reviews addressed exact OPC principal schema, authority freshness,
  domain binding, finality head agreement, active-call shutdown and cross-owner
  behavior. The actual Linux privilege boundary remains a live deployment gate.
- Relevant local regression: Monad479, Core budget/recovery/migration21,
  contracts64 (including 128,000 invariant calls), Commerce25, Review76,
  submission6, canonical OPC15 and Node MCP10 passed. Final focused frontend,
  hosted security and server27 passed. Deprecation/lint warnings do not affect
  these successful results.
- Fresh local isolated-role identity/key/DryRun checks passed; no actual new
  signature or transaction was generated. Concrete unsigned two-CREATE proposal
  and bounded nine-purchase scope are in docs/monad/public-deployment-plan-20261008.md.
- GCP OS Login is currently blocked for the existing operator account; an
  administrator must restore access. No account switch, IAM/Key Policy change,
  successful SSH key addition, instance metadata workaround or remote rollout
  was performed. The previous real delivered order is preserved. HTTPS deployment,
  new contracts, live two-wallet acceptance, revocation/refusal and videos are
  still pending; do not claim the public product is complete.
- Final independent UI review found no blocker after generation/owner guards and
  serial session/logout cookie writes. Real backend revocation semantics are
  retained: old cookies cannot restore a revoked Core browser session.11 UI
  tests passed, covering late order/signature/session responses and fresh-owner
  reconnection. Source scope/secret-pattern scan checked893 files with no finding.
- Final GitNexus staged detection:43 files,987 symbols,31 affected flows,
  CRITICAL aggregate risk. Root reported this before committing and checked the
  actual Git diff: only the expected new hosted/deployment/faucet/gate/test files
  and three existing documentation files changed. The CLI also reports shared
  README heading/symbol names in unmodified files; its name mapping is not a
  substitute for the reviewed file diff. No canonical copied Core/Node/Marketplace
  business source or Clink checkout was modified. No live risk gate is waived.
