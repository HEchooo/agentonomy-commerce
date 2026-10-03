# Agentonomy Commerce on Monad — submission draft

**Status: blocked draft.** This folder is preparation material for a possible Monad testnet submission. It is not an official submission, deployment certificate, audit, rights declaration, or adoption report. The local Anvil/EVM, loopback HTTP, unified MCP, and browser UI paths now pass; the full relevant regression suite also passes. Public testnet acceptance remains pending.

## Project

Agentonomy Commerce lets an agent purchase a fixed service within a user-approved budget. The Monad work adds a narrow ordinary ERC-20 rail:

1. Core verifies wallet identity, the existing signed Spending Grant, policy/risk, the merchant scope, and the budget reservation.
2. The user signs an EIP-712 `SpendGrant` that fixes the token, payee, per-payment cap, total cap, validity window, agent scope, and execution signer.
3. Core authorizes a single `PurchaseExecution` over an immutable purchase ID, quote hash, amount, and deadline.
4. `AgentonomyBudgetExecutor` checks both signatures, revocation, replay state, caps, and exact ERC-20 balance deltas.
5. A watcher independently checks two RPC observations, the receipt, a canonical block matching the receipt, `PaymentExecuted`, `Transfer`, and the same explicit finality boundary block before Core marks payment verified.
6. Marketplace delivers a CSV reconciliation result over real loopback HTTP in the local composition. Delivery retry never creates a second payment.

The rail uses an ordinary ERC-20 executor. EIP-7702 is outside the first version. Core remains the authority; the contract is a user-enforced ceiling, not a replacement ledger or a general wallet.

## Evidence boundary

| Item | Current state |
| --- | --- |
| Monad testnet | Chain ID `10143` (`0x279f`) read by two documented RPC endpoints on 2026-10-03 |
| Finality rule | Adapter currently uses Monad `Verified = finalized - 3`; this must be rechecked before deployment |
| Token address | **Pending**; the local `TestUSD` asset is not official USDC |
| Executor address | **Pending** |
| Payee address | **Pending** |
| Public transaction | None recorded |
| Public service URL | None recorded |
| Local EVM/HTTP/MCP/UI acceptance | Passing local composition; final regression record is [`acceptance.md`](../../docs/monad/acceptance.md) |
| Adoption, revenue, partnership, audit | None claimed or recorded |

Read [the source baseline](../../docs/monad/source-baseline.md) and [deployment notes](../../docs/monad/deployment.md) for the exact limitations. The protocol is documented in [design.md](../../docs/monad/design.md); the threat model is [here](../../docs/monad/threat-model.md).

## Local reproduction

Use Python 3.12 and an isolated environment. The local mode starts Anvil, creates temporary test accounts in memory, deploys `AgentonomyTestUSD` and the budget executor, and sends real local EVM transactions with no real funds or public-chain signers.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps -e apps/node
forge build --root contracts
forge fmt --root contracts --check
forge test --root contracts

PYTHONPATH=.:apps/node:apps/core:apps/facilitator \
  .venv/bin/python -m pytest -q tests/monad
```

The current local EVM composition covers actual contract execution, Core/Marketplace/watcher verification, loopback HTTP delivery and replay, the unified `clink_node` MCP path, and browser UI flow. Final broad regression commands and results are kept in the root-owned [acceptance record](../../docs/monad/acceptance.md); this draft does not repeat obsolete provisional counts.

The existing Commerce and review compositions remain separate and explicitly simulated where documented. The Monad local composition is labelled `local_anvil` and uses TestUSD; a local receipt, Anvil transaction, or synthetic key is not public-chain evidence.

## Submission materials

- [Three-minute technical demo script](technical-demo-script.md)
- [Two-minute pitch script](pitch-script.md)
- [Market and adoption plan](market-and-adoption.md)
- [Rights review](rights-review.md)
- [Installed dependency license inventory](dependency-licenses.json)
- [Developer feedback form](developer-feedback.md)

The team name is **Agentonomy** and the working contact is `fengjie@alvinsclub.ai`. No Telegram handle is supplied in this draft. The source tree is standalone and has no runtime dependency on an outside Clink checkout. Any official form, public URL, team identity, logo use, or late-submission statement remains outside this draft.

## Claim discipline before submission

Replace a pending field only with fresh, independently checkable evidence from the exact clean commit being submitted. In particular, do not describe local TestUSD as USDC, do not turn a source snapshot into a public deployment, and do not add customers, partners, audits, revenue, or external developer feedback that has not actually been recorded.

The working plan records indicative weights of technical 20%, developer experience 20%, originality 15%, market readiness 25%, and adoption/follow-through 20%. Verify the current official rules and form immediately before submission; these weights are not an acceptance promise.
