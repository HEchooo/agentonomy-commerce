# Monad demonstration and recording checklist

The two-contract Monad deployment and the four-step OKX wallet setup are
complete. A live MCP search/details/preview attempt initiated one execute request
and submitted a `0.30 TestUSD` payment; Core plus the budget watcher independently
verified the settlement. The outer Marketplace request timed out after 45
seconds before receiving Core's completed response. After restart, the same
original order was recovered, and same-order query replay returned the identical
delivered result without another payment; the API order state is `delivered`.
`delivered_and_replayed` is the acceptance evidence state for this recovery/query
replay, not an API order enum. Core-plus-chain
revocation, recording and submission remain pending. The existing review site's
simulated purchase does not substitute for a Monad transaction. Configuration
is generated, the operator UI previously completed owner authorization, and the
local live API is running at `http://127.0.0.1:8091/`.

## Current live checkpoint

- Owner: `0x59899831691aa79507818961773497c751bffc8b`; chain: `10143`.
- Finite `1.00 TestUSD` approval: [`0xed3026bccfca3d9edebd9bde03aa674cf102c6f41dae03125e8c1e8812c5dbeb`](https://testnet.monadexplorer.com/tx/0xed3026bccfca3d9edebd9bde03aa674cf102c6f41dae03125e8c1e8812c5dbeb), block `68981135`.
- Live order: `preview_739a74c933eb` → `purchase_739a74c933eb`, reservation `reserve_c154873b164f`.
- Payment: [`0x2711ae051fbf132e544e984c11db6b3b8b518caa40a70d380520743159408037`](https://testnet.monadexplorer.com/tx/0x2711ae051fbf132e544e984c11db6b3b8b518caa40a70d380520743159408037), receipt status `1`, block/finality block `68984424`, relayer transaction nonce `2`.
- The status projection's `settlement_submissions` is the relayer pending nonce,
  not a purchase count: baseline `2` → `3` after this purchase (`2` deployments
  plus `1` purchase).
- Recovery evidence: state `delivered_and_replayed` for API order state
  `delivered`; same order/payment and CSV input hash; delivery count `0 → 1`,
  then remained `1` on same-order query replay.
- Budget after replay: used `0.30`, reserved `0.00`, remaining `0.70` TestUSD.
- Report: 2 input rows; income `10.00`, expenses `2.00`, net `8.00` USD.
- Timeout fix `4db0ff5` is independently approved. Public request ceilings are
  `Core 180s < Marketplace 240s < HTTP 270s`; local and simulation remain
  `45s`. These are per-layer ceilings, so serial Core calls can still exceed an
  outer deadline; unknown outcomes remain inspect/recover-only for the original
  order.

The prior UI run completed owner authorization. The UI change at `32f0211`
was independently reviewed **APPROVE**, with **8 UI tests passed**. Main-agent
focused UI/API/submission verification reported **21 passed** and two dependency
warnings. A main-agent real browser reload, original-order query, and
post-`refreshStatus` check confirmed API order state `delivered`, the unchanged
report, budget `0.30/0.70`, pending nonce `3`, delivery count `1`, the Monad
`10143` payment verification summary, and the correct payment tx link rather
than the approval tx; JavaScript errors were empty. The objective legacy
merchant-label prompt is recorded, and the UI display fix is accepted.
Restart recovery and same-order query replay are evidenced above; recording
still requires capturing the successful flow. The evidence is API recovery
plus identical GET/query output; it does not claim a second MCP execute.
The returned service result carries `settlement_mode=local_anvil` as a
historical merchant label preserved in the original hashed report. The payment proof remains chain `10143`
and is not characterized by that label. The public review site remains a
simulation.

## Before recording

1. Verify both deployed contracts and their creation transactions through two
   RPCs. Keep explorer links, code hashes, source commit and verification time.
2. Use the generated configuration and confirm the loopback operator page while
   the local API is running at `http://127.0.0.1:8091/`. The prior UI run
   completed owner authorization; confirm chain `10143`, buyer
   `0x59899831691aa79507818961773497c751bffc8b`, actual TestUSD and Executor
   addresses, and that the isolated role session remains valid. The successful
   recovery/query replay is already evidenced; capture it rather than creating
   a new order.
3. The wallet identity, business budget, EIP-712 budget grant and finite
   **1.00 TestUSD** approval are complete. Preserve their evidence; the grant
   lasts one day, permits at most **0.50** per payment, and fixes the merchant
   and execution signer.
4. Preserve the state directory and original order IDs. Refreshing or restarting
   must restore state; deleting it is not a recovery demonstration.

TestUSD has no monetary value. A test MON balance is needed separately for
wallet transactions and the Gas relayer. Never show AWS session files, signed
grants, raw signed transactions, private runtime databases or wallet recovery
material in a recording.

## Technical demo, at most three minutes

| Time | Show | Point to explain |
| --- | --- | --- |
| 0:00–0:25 | Product and Monad network/contract addresses | Agentonomy lets an Agent buy a useful service within a user-authorized budget. |
| 0:25–0:55 | Budget terms and completed wallet authorization | The user fixes spending limits; the Agent does not receive the wallet key. |
| 0:55–1:40 | Preview a CSV reconciliation report, purchase for 0.30 TestUSD, open payment transaction and delivered result | Core authorizes, the contract executes, two RPCs verify, and Marketplace returns the HTTP service result. |
| 1:40–2:15 | Reload/restart, query or recover the same order | Order ID and transaction hash stay the same; recovery does not charge again. |
| 2:15–2:50 | Revoke the grant and show a new purchase being refused | Core and onchain revocation are separate confirmations; already-paid orders remain readable. |
| 2:50–3:00 | Repository and public evidence links | Identify exactly what ran on Monad and what remains a local test. |

Revoke remains a user OKX action and must happen only after capturing the
successful purchase and recovery/query footage. Keep at least **0.30** unspent when
demonstrating revocation, so rejection cannot be confused with an exhausted
budget. With the current **0.70** balance, make at most one additional **0.30**
purchase before revocation. A revoked grant is not re-enabled by reload.
Each new purchase consumes another 0.30; recovery of an existing paid order does
not. Never create a fresh order to disguise a failed recovery.

Use the unified `clink_node` MCP for the Agent demonstration. Its purchase tools
share this same Core and Marketplace flow. The wallet operator page handles
owner signatures; it is not an unrestricted Agent signing interface.

Suggested Agent prompt after wallet setup:

> 通过 clink_node 查找 CSV 对账服务，为下面的数据生成报告。先取得报价；
> 如果价格不超过 0.30 TestUSD 且在我已签署的预算内，完成购买并返回报告、
> 订单 ID 和 Monad 交易哈希。若授权不足，停止并告诉我缺哪一步。若付款后
> 尚未交付，只恢复原订单，不创建替代付款。
>
> transaction_id,date,description,amount,currency,category
>
> 1,2026-10-07,Sale,10.00,USD,sales
>
> 2,2026-10-07,Fee,-2.00,USD,fees

Show the Agent's actual tool calls and returned result in the recording. Do not
substitute a prewritten answer or slides for the running product.

## Pitch, at most two minutes

Explain the problem (giving an Agent a wallet key is too broad), the user flow
(authorize a bounded budget, buy a service, receive the result), the trust
boundary (Core policy plus contract limits), and the evidence (real Monad
payment, delivery and recovery without double charging). Close with the target
users: Agent applications that buy APIs, reports or other metered services.

Do not claim production adoption, revenue, a security audit, mainnet settlement
or public deployment based on local Anvil tests. Record the two videos only
after the live acceptance results are available, then put their actual URLs in
the hackathon form. A saved partial form is not a complete submission.
