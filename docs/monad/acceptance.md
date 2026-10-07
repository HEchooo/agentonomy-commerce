# Monad budget commerce acceptance

Run date: 2026-10-03. Development base: `029cd0b`, branch `codex/monad-budget-commerce`.

This is an **as-of 2026-10-03 pre-live local acceptance record**. Current Monad
deployment, one verified `0.30 TestUSD` payment, delivered order and same-order
recovery/query replay are recorded in [`role-session-acceptance.md`](role-session-acceptance.md).

This record separates real local EVM execution from public Monad acceptance. New payment tests start an owned Anvil process, deploy actual TestUSD/executor bytecode, sign real transactions and independently inspect receipts. The testnet profile and two-RPC verifier are code, not evidence of public deployment.

## Verification gates

Fresh final regression results (same isolated repository environment):

| Scope | Result |
| --- | --- |
| Solidity executors/deployment/invariant | 54 passed; invariant 256 runs / 128,000 calls |
| New Monad protocol, backend, real chain/HTTP/MCP/API and recovery | 71 passed |
| Core full suite, including new budget/migration tests | 2,116 passed |
| Budget/migration focused gate (included in Core total) | 21 passed |
| Existing Commerce / Review / submission package | 16 / 76 / 6 passed |
| Workspace / existing E2E | 12 / 2 passed |
| Node | 841 passed, 1 skipped |
| Hosted | 522 passed, 4 skipped |
| Marketplace | 320 passed |
| Prediction Markets selected regressions | All 16 smoke scripts passed |

Five skips require explicitly configured PostgreSQL test databases (Node access-revocation and Hosted repository/migration tests). This dated run verifies the SQLite local profile; it makes no PostgreSQL or public Monad acceptance claim. Framework deprecation and Python fork/thread warnings were emitted; there were no test failures in the final commands. Early sandbox socket failures were rerun successfully with loopback access. One test-import path collision introduced during development and the new migration-head expectation were corrected before the full Core run.

Commands:

```sh
make PYTHON=.venv/bin/python test-commerce test-review test-submission
make PYTHON=.venv/bin/python test-workspace test-node test-e2e test-apps test-hosted
make test-contracts
make PYTHON=.venv/bin/python test-monad
```

The Go installer is unchanged; its platform build is outside this change. Browser checks use a fresh isolated session at `http://127.0.0.1:8090`, never a personal browser profile.

## Evidence to retain

- Contract rejection/rollback tests and nonvacuous invariant handler results.
- Codec fixed vectors, strict success/reverted receipt proofs, two-RPC disagreement and finality cases.
- Core scope/binding, signed-attempt durability, budget accounting and migration round trip.
- Actual MCP → Core → EVM → watcher → HTTP merchant delivery and replay.
- Broadcast/restart and paid-delivery-failure recovery without a second payment.
- UI budget, transaction, delivery and revocation states.

## Public acceptance — pending as of 2026-10-03

As of this dated record, no public Monad transaction or deployed address was
claimed. The later deployment, wallet setup, canary payment and recovery evidence
are in [`role-session-acceptance.md`](role-session-acceptance.md). This record still
does not claim an external user, revenue, audit or public service deployment;
license choice and final submission review remain pending.


## Concrete acceptance outcomes

- A real local signed transaction moved 0.30 TestUSD; Core used budget changed 0.00 → 0.30 and reserved budget returned to 0.00.
- Restarting Marketplace/Core before payment verification recovered the same purchase/tx and exactly one onchain PaymentExecuted event.
- A real merchant HTTP 503 after payment recovered after process restart with the same order and one payment. Input is retained for a bounded 24-hour retry window; after expiry, report unavailable input and never charge again.
- Three 0.30 purchases succeeded; the fourth was rejected with BUDGET_EXCEEDED and no fourth payment.
- A conflicting second RPC observation kept 0.30 reserved until both observations agreed.
- A real mined status-0 transaction produced strict independently checked revert proof. Core unit cases release only verified final-revert reservations, exactly once; loose failure strings remain pending.
- Contract tests independently reject wrong payee, expiry, revocation, limits, replay and malicious token behavior without relying on a frontend.
- Full rehearsal exported `.artifacts/monad-local-rehearsal.json`: delivered; used/replayed budget both 0.30; Core and chain revocation both confirmed. This file is deliberately local evidence, not a public Monad receipt.
- Desktop and 390px viewport inspected. No horizontal overflow. Quote freezes input; refresh does not buy; explicit purchase and later refresh/recovery leave the second order's total used budget unchanged at 0.60. Browser needs no backend token.

## Review and recovery limits

Multiple agent code/spec reviews were performed; this is not a third-party audit. Review findings led to mandatory pre-broadcast init-code verification, strict common RPC finality boundaries, real revert proof, immutable Core grant reverse binding, signed-attempt validation and delivery-only recovery.

A crash after a committed broadcast claim but before its outcome is known remains verification-only and may require manual investigation. It never creates a replacement payment. In this dated local record, public wallet onboarding/external signer composition was still pending; the later controlled role-session evidence is recorded separately. The ephemeral-key local launcher still rejects public networks.

Final review also tightened public Core attempt projection, stale local browser session handling and read-only preflight agreement on the same canonical finality boundary. These fixes passed the final 71-test Monad gate and 21-test Core budget gate.

## Local repository handoff

Implementation commit `87f7538` was fast-forwarded into the original `agentonomy-commerce` checkout. From that directory and its own virtual environment, `make PYTHON=.venv/bin/python test-monad` passed again: 71 Monad tests and 21 Core budget tests. The original plan and local evidence are retained under `.artifacts/monad-implementation/`. As of this dated handoff, no remote push, public deployment or final submission had occurred; current public evidence is recorded separately in [`role-session-acceptance.md`](role-session-acceptance.md).
