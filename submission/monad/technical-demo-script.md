# Three-minute technical demo script

**Recording status:** live Monad testnet payment and delivery evidence is
available. The operator UI, API and merchant run over loopback HTTP at
`http://127.0.0.1:8091/`; the public review service remains explicitly
simulated, and no public product endpoint is claimed. Use the already-paid
order below. Do not execute that order again. This is the current evidence
walkthrough; revise it for the accepted public multi-wallet flow before final
hackathon recording and publication.

## 0:00–0:20 — state the product and evidence boundary

Open the loopback operator page and show chain `10143`, the owner, the
deployed TestUSD and BudgetExecutor addresses, and the current budget summary.
Do not show private keys, raw signed grants or runtime databases.

Say:

> Agentonomy Commerce lets an Agent buy a fixed service inside a user-approved
> budget. This run has a real Monad testnet payment, while the operator UI,
> API and merchant stay on loopback HTTP. The public review site is still a
> simulation.

## 0:20–0:45 — show the control path

Display:

```text
Agent → clink_node MCP → Marketplace → Core
     → Monad BudgetExecutor → two-RPC watcher → loopback HTTP merchant
```

Say:

> Core remains the control plane for identity, the existing Spending Grant,
> policy and risk, budget reservation, execution and audit. The Monad adapter
> binds that flow to an ordinary ERC-20 executor. It does not create a second
> wallet ledger or an unrestricted Agent signing interface.

## 0:45–1:05 — show the bounded authorization

Show the completed owner authorization and the finite `1.00 TestUSD`
allowance. Point out that the grant fixes the token, payee, Agent scope,
`0.50` per-payment limit, `1.00` total limit, validity window and execution
signer. TestUSD is a self-deployed test asset, not official USDC. Keep raw
signature material off screen.

## 1:05–1:40 — query the paid order

Use the actual read-only MCP calls `get_clink_purchase` and
`clink_node_status` in the MCP client connected to the running operator API. Query the existing
order `purchase_739a74c933eb` and its preview `preview_739a74c933eb`; use the
status call to show the budget and verified-payment counters. Do not click
`execute` or create a replacement order.

Show the delivered CSV report, the Monad `10143` payment verification summary,
and the single payment transaction
`0x2711ae051fbf132e544e984c11db6b3b8b518caa40a70d380520743159408037`.
Point out receipt status `1`, block `68984424`, verification through both RPCs,
delivery count `1`, and the remaining `0.70 TestUSD`.

Say:

> This order was paid once. The report is delivered, and the link points to
> the payment transaction rather than the allowance approval transaction.

## 1:40–2:10 — reload and recover the same order

Reload the page, query the same order, and finish the `refreshStatus` path.
If the prepared recovery control is shown, use `recover{}` for this same order
before the final GET/query, then repeat only the read-only
`get_clink_purchase`/`clink_node_status` calls. Show that the API state is
`delivered`, the report is unchanged, the CSV input hash is unchanged, and
delivery remains `1`.

Say:

> Recovery and query replay returned the same order, payment and report. There
> was no second payment and no second MCP execute. `delivered_and_replayed` is
> the acceptance evidence label for this check; the API order state is simply
> `delivered`.

## 2:10–2:35 — connect the UI to verifiable chain evidence

Show the explorer links for the TestUSD deployment, BudgetExecutor deployment
and payment. Explain that the watcher compared two RPC observations, the
receipt block, the payment and transfer events, and the finality boundary.
The report retains the historical merchant label `settlement_mode=local_anvil`;
that label does not describe the verified Monad payment mode.

## 2:35–2:50 — state the remaining controlled action

Do not start another purchase while recording this accepted path. If the user
chooses one optional new purchase later, the remaining `0.70` permits at most
one more `0.30` payment while preserving at least `0.30` for the revocation
refusal check. Revocation itself requires the user's OKX action after the
successful purchase/recovery footage.

## 2:50–3:00 — close with honest status

Say:

> The payment and same-order recovery are independently inspectable on Monad
> testnet. The UI and API are local loopback components, the public review
> service is simulated, and recording, OKX revocation and final submission are
> still pending.

End on [`docs/monad/role-session-acceptance.md`](../../docs/monad/role-session-acceptance.md)
and [`docs/monad/recording-checklist.md`](../../docs/monad/recording-checklist.md).
