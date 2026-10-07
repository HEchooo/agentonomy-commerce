# Agentonomy Commerce on Monad — submission draft

**Status: blocked draft.** This folder is preparation material for a possible Monad testnet submission. It is not an official submission, deployment certificate, audit, rights declaration, or adoption report. The Monad testnet contracts are deployed and verified through two RPC observations, and one user-authorized `0.30 TestUSD` payment was verified on chain `10143` and delivered once. Full recording, user OKX revocation, final-form submission and a reviewer-accessible HTTPS product URL remain pending; the public review service remains simulated.

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
| Monad testnet | Chain ID `10143` (`0x279f`); the payment receipt and finality boundary were verified through two RPC observations |
| TestUSD deployment | `0x6f36d16b27713f7b1a7e1d2773420fca9fd494c9`, CREATE [`0x441be12ebb19c739b041898ceb2780e6317103d4dbd20492e9462e638bfa676b`](https://testnet.monadexplorer.com/tx/0x441be12ebb19c739b041898ceb2780e6317103d4dbd20492e9462e638bfa676b), block `68930167` |
| BudgetExecutor deployment | `0x391b21c017d8938820c5744ac70603857df8970c`, CREATE [`0x5a9dd34eb22039218f9af880d838fc1370487079656533bde05f24fa65c61016`](https://testnet.monadexplorer.com/tx/0x5a9dd34eb22039218f9af880d838fc1370487079656533bde05f24fa65c61016), block `68933987` |
| OKX owner / buyer | `0x59899831691aa79507818961773497c751bffc8b`; finite `1.00 TestUSD` approval is recorded in the role acceptance evidence |
| Monad testnet payment | [`0x2711ae051fbf132e544e984c11db6b3b8b518caa40a70d380520743159408037`](https://testnet.monadexplorer.com/tx/0x2711ae051fbf132e544e984c11db6b3b8b518caa40a70d380520743159408037), receipt status `1`, block/finality block `68984424`, verified through both RPCs |
| Order and delivery | `purchase_739a74c933eb`; API state `delivered`; the two-row CSV report was delivered once, and same-order recovery/query replay returned the same result without another payment |
| Budget after delivery | Used `0.30`, reserved `0.00`, remaining `0.70` TestUSD; pending nonce `3` represents two deployments plus one purchase |
| Local EVM/HTTP/MCP/UI acceptance | Passing local composition; the operator UI/API and merchant are loopback services at `http://127.0.0.1:8091/` |
| Reviewer-accessible product URL | **Pending multi-wallet implementation/deployment**; user chose own-wallet reviewer access |
| Public review service | Simulation only; it is not the live Monad product URL |
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

The current local EVM composition covers actual contract execution, Core/Marketplace/watcher verification, loopback HTTP delivery and replay, the unified `clink_node` MCP path, and browser UI flow. The standalone `apps/core`, `apps/marketplace`, `apps/node` and `examples/monad_commerce` tree carries the copied Clink business flow plus the Monad adapter, configuration and watcher; it has no runtime dependency on an outside Clink checkout. Final regression commands and live evidence are kept in the root-owned [role-session acceptance](../../docs/monad/role-session-acceptance.md) and [recording checklist](../../docs/monad/recording-checklist.md).

The existing Commerce and review compositions remain separate and explicitly simulated where documented. The payment above is Monad testnet evidence; the loopback HTTP UI/API and merchant are local. A local receipt, Anvil transaction, or synthetic key is not public-chain evidence. The delivered report retains the historical merchant label `settlement_mode=local_anvil`; that label does not describe the Monad payment mode.

## Submission checkpoint

The authenticated submission draft was saved on **2026-10-07 15:22 UTC**; the
confirmed deadline is **2026-10-14 11:59 GMT+8**. The current checklist is
**3/5** complete, with primary track **Trust, Identity & AI Infrastructure**,
project details and logo complete. The live product HTTPS URL, technical demo
URL and pitch URL are still empty, so the form has not been finally submitted.
The user chose own-wallet reviewer access. Website login and OPC binding are
under design review; see [public-live-readiness.md](../../docs/monad/public-live-readiness.md).
The actual purchase UI/API remains loopback, while the public review URL is simulated.

## Submission materials

- [Three-minute technical demo script](technical-demo-script.md)
- [Two-minute pitch script](pitch-script.md)
- [Market and adoption plan](market-and-adoption.md)
- [Rights review](rights-review.md)
- [Saved form draft](form-draft.md)
- [Exact saved fields](form-saved.json)
- [Current official form/track requirements](official-requirements.json)
- [Public evidence data](public-evidence.json)
- [Installed dependency license inventory](dependency-licenses.json)
- [Developer feedback form](developer-feedback.md)

The team name is **Agentonomy** and the working contact is `fengjie@alvinsclub.ai`. No Telegram handle is supplied in this draft. The source tree is standalone and has no runtime dependency on an outside Clink checkout. The UI evidence was verified against implementation commit `32f0211dc4efc0c602c990b4171da67cc51f0eb3`; the final submission package and its public product URL remain pending.

## Claim discipline before submission

Replace a pending field only with fresh, independently checkable evidence from the exact clean commit being submitted. In particular, do not describe self-deployed TestUSD as official USDC, do not turn loopback UI/API or the simulated public review service into a public service deployment, and do not add customers, partners, audits, revenue, or external developer feedback that has not actually been recorded.

The selected-track page was rechecked on 2026-10-07: technical 20%, developer design/craft 20%, originality/track insight 15%, market readiness 25%, and traction/path forward 20%. See the observed requirements above; these weights are not an acceptance promise.
