# Monad budget commerce progress

Plan: docs/superpowers/plans/2026-10-03-monad-budget-commerce.md
Base: 029cd0b. Branch: codex/monad-budget-commerce.
Worktree: /private/tmp/agentonomy-commerce-monad-20261003.

## Constraints
- Standalone copied runtime; no sibling Clink runtime imports.
- No public-chain broadcasts, production changes, final submission or license grant.
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
- [ ] Public Monad acceptance (requires user later).

## Evidence
No public testnet deployment/transaction claimed.
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
