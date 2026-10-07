# Monad demonstration and recording checklist

Public acceptance is still pending. Use this sequence only after actual
deployment and wallet setup; the existing review site's simulated purchase
does not substitute for a Monad transaction.

## Before recording

1. Verify both deployed contracts and their creation transactions through two
   RPCs. Keep explorer links, code hashes, source commit and verification time.
2. Start the loopback operator page in an OKX-enabled browser. Confirm chain
   `10143`, buyer `0x59899831691aa79507818961773497c751bffc8b`, actual TestUSD and
   Executor addresses, and that the isolated role session remains valid.
3. Complete wallet identity, business budget, EIP-712 budget grant and finite
   **1.00 TestUSD** approval. The grant lasts one day, permits at most **0.50**
   per payment, and fixes the merchant and execution signer.
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

Revoke only after capturing the successful purchase and recovery footage. Keep
at least **0.30** unspent when demonstrating revocation, so rejection cannot be
confused with an exhausted budget. A revoked grant is not re-enabled by reload.
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
