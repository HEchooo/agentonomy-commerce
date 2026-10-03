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
- Final candidate is ready for local fast-forward integration; merged-location smoke remains the final handoff gate.
