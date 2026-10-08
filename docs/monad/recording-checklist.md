# Monad demonstration and recording checklist

Status on 2026-10-08 (Asia/Shanghai): the new hosted release is deployed on
the dedicated review VM, both CREATE journals are complete, and the ERC-8004
registration is complete. The service runs as an active systemd web process on
`127.0.0.1:8092` behind Caddy at `https://review.agentonomy.xyz`. The external
HTTPS identity probe and certificate check pass; all user-wallet acceptance
remains pending, so recording must wait for the live buyer flow.

The local one-wallet Monad payment and recovery evidence below is preserved as a
historical canary. It uses the earlier contracts and order and does not satisfy
the new public purchase, recovery, revoke, second-wallet, feedback or recording
gates.

## Current public rollout checkpoint

- Release: `44fdd9fb0d228c2b6b58d4673e8ecb54bf09ba76` on
  `agentonomy-commerce-review-01` in `blockchain-nodeservice`,
  `asia-southeast1-b`.
- Web service: separate `agentonomy-web` and `agentonomy-sign` users; loopback
  web port `8092`; Caddy HTTPS origin `https://review.agentonomy.xyz`; the
  `agentonomy-web` unit is active/running and its backend listens only on
  `127.0.0.1:8092`. The old simulated Docker container is not running with
  data retained and the startup script was removed.
- SSH metadata remains `enable-oslogin=FALSE` and
  `block-project-ssh-keys=TRUE`; the existing eight-hour VM public key expires
  at `2026-10-08T14:21:05Z`. The temporary single-IP SSH rule was removed; only
  the original `agentonomy-commerce-iap-ssh` TCP/22 rule from `35.235.240.0/20`
  targeting `commerce-review-admin` remains.
- TestUSD (CREATE nonce 3): address
  `0x1bf06ce9eeeb9e998cecf96cd46f1a7e5bed547a`, [CREATE transaction](https://testnet.monadexplorer.com/tx/0x1c7f7a6d4506e87f1572952fff740837b338451ab63063594f5424996e495d67),
  block `69187111`, runtime hash
  `0xe587fc80f9f476b07b58fdef987df555b1612d81ff66a5be800bdf695dc9c167`,
  Verified boundary `69189452`.
- Budget executor (CREATE nonce 4): address
  `0x7a87b04c67c11afa7ce1a27bdb3c1c1ca55e1aa4`, [CREATE transaction](https://testnet.monadexplorer.com/tx/0xb31a1954f60a2947bbeb6f67c84fbe481bf3968e20249640238e76a1de360e41),
  block `69189585`, runtime hash
  `0x6bf7d58744627ed56b89134b6ce354981f4638a74a8338f9b0376c488bdb70d2`,
  Verified boundary `69189718`.
- Both CREATE journals report `complete`; runtime, state, constructors, receipt
  and finality checks passed through both RPCs. The latest rollout gates were
  592 Monad tests in 45.11 seconds and 21 Core budget tests in 2.34 seconds.
- ERC-8004 registration: nonce `5`, [registration transaction](https://testnet.monadexplorer.com/tx/0x039583372a8e324da28e1e8ed278ee7d57967235bae2496b9bd91bcd9592a2f0),
  block `69189943`, Agent ID `2073`, finality block `69191573`, canonical hash
  `0x32648ee0d7297e399da3f57cb294a8b6be4f20839ed29e75518a546903137bea`.
  Identity verification at block `69191587` binds owner/receiving wallet
  `0xbdcb39ac5ff83485cb35160f0ddaa0b7446ee009` to
  `https://review.agentonomy.xyz/agent.json`.
- External HTTPS and loopback `agent.json` both report `active=true` with
  `registrations=[agentId=2073,agentRegistry=eip155:10143:0x8004a818bfb912233c491871b3d84c89a494bd9e]`,
  `x402Support=false` and `supportedTrust=[reputation]`. The well-known
  registration document matches the same identity. The hosted wallet page
  returned HTTP 200 with connect/OPC-approve identifiers, CSP and
  `Cache-Control: no-store`; anonymous `/api/status` returned `401`. A fresh
  browser page had no console errors or warnings. These are endpoint checks,
  not wallet consent or purchase evidence.
- The public purchase window is exactly nonces `6` through `13`, eight purchase
  transactions after registration nonce `5`. The buyer grant is one day, with
  `1.00` TestUSD total and `0.50` maximum per payment. The target purchase is
  one `0.30` CSV report.

## Public acceptance steps before recording

1. Preserve the completed external HTTPS probe. Confirm that `/agent.json` and
   `/.well-known/agent-registration.json` expose the expected registration
   metadata: Agent ID `2073`, the fixed Identity registry, the HTTPS web
   endpoint in `services`, `active=true`, `x402Support=false` and
   `supportedTrust=[reputation]`. Confirm the owner, receiving wallet and URI
   from the independent onchain registration proof above, or from the
   post-login service-identity display; do not infer those fields from the JSON.
   This gate is complete; it does not imply a wallet purchase.
2. With buyer wallet `0x59899831691aa79507818961773497c751bffc8b`, perform a
   fresh HTTPS EIP-191 login. Complete one 1.00 TestUSD claim, the business
   budget and EIP-712 grant signature, a finite 1.00 TestUSD allowance, and
   wallet-approved hosted OPC installation consent.
3. Use the unified `clink_node` MCP to discover and preview the CSV service, then
   purchase exactly 0.30 TestUSD. Capture the order, payment transaction,
   receipt/finality evidence, Core receipt and delivered CSV result.
4. Query or recover that same order after a refresh or restart. Confirm the
   order ID, payment hash and input hash are unchanged and that delivery does
   not submit a second payment.
5. Revoke Core and onchain authorization, then show a new purchase refused while
   the already-paid order remains readable. Verify a second wallet cannot read
   or spend the first wallet's budget or orders. Optional ERC-8004 feedback is a
   separate explicit wallet action.

Preserve the original order and state directory during the demonstration. A
refresh, timeout or restart must recover the existing order; never create a new
order to disguise a failed recovery. TestUSD has no monetary value, but wallet
transactions and the relayer require test MON. Never show AWS session files,
signer configuration, raw signed transactions, browser tokens, private runtime
databases or wallet recovery material.

## Technical demo, at most three minutes

| Time | Show | Point to explain |
| --- | --- | --- |
| 0:00–0:25 | Public HTTPS origin, active Agent metadata, Monad network and contract links | The deployed service identity and payment contracts are independently pinned. |
| 0:25–0:55 | Fresh wallet login, grant terms, finite allowance and OPC consent | The user fixes spending limits; the Agent never receives the wallet key. |
| 0:55–1:40 | `clink_node` preview, 0.30 TestUSD purchase, payment transaction and delivered CSV | Core authorizes, the contract executes, two RPCs verify, and Marketplace returns the HTTP service result. |
| 1:40–2:15 | Refresh/restart and query or recover the same order | The order ID and transaction hash stay the same; recovery does not charge again. |
| 2:15–2:50 | Revoke and refused new purchase, then second-wallet isolation or feedback disclosure | Core and onchain revocation are separate confirmations; the paid order remains readable. |
| 2:50–3:00 | Repository and public evidence links | Identify exactly what ran on Monad and which gates remain outside the recording. |

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

Show the Agent's actual tool calls and returned result. Do not substitute a
prewritten answer or slides for the running product. Do not claim production
adoption, revenue, a security audit, mainnet settlement or completed public
acceptance from local Anvil tests or the historical canary. Add video URLs to
the hackathon form only after the live acceptance results exist.

## Historical local one-wallet canary

This section records the earlier real Monad testnet payment and restart/query
recovery. It is retained for provenance and recording preparation, but it is not
evidence that the new public hosted wallet flow has been accepted.

- Owner: `0x59899831691aa79507818961773497c751bffc8b`; chain: `10143`.
- Finite `1.00 TestUSD` approval: [`0xed3026bccfca3d9edebd9bde03aa674cf102c6f41dae03125e8c1e8812c5dbeb`](https://testnet.monadexplorer.com/tx/0xed3026bccfca3d9edebd9bde03aa674cf102c6f41dae03125e8c1e8812c5dbeb), block `68981135`.
- Historical order: `preview_739a74c933eb` → `purchase_739a74c933eb`, reservation `reserve_c154873b164f`.
- Historical payment: [`0x2711ae051fbf132e544e984c11db6b3b8b518caa40a70d380520743159408037`](https://testnet.monadexplorer.com/tx/0x2711ae051fbf132e544e984c11db6b3b8b518caa40a70d380520743159408037), receipt status `1`, block/finality block `68984424`, relayer transaction nonce `2`.
- Recovery evidence: state `delivered_and_replayed` for API order state
  `delivered`; same order/payment and CSV input hash; delivery count `0 → 1`,
  then remained `1` on same-order query replay.
- Historical budget after replay: used `0.30`, reserved `0.00`, remaining
  `0.70` TestUSD. Report: two input rows; income `10.00`, expenses `2.00`, net
  `8.00` USD.
- The outer Marketplace request timed out after 45 seconds before receiving
  Core's completed response. After restart, the original order was recovered;
  the evidence is API recovery plus identical GET/query output, not a second MCP
  execute.
- The historical result carries `settlement_mode=local_anvil` as a merchant
  label preserved in the original hashed report. The payment proof remains on
  chain `10143`; this label does not characterize the chain settlement.
- The UI run at change `32f0211` completed owner authorization and was reviewed
  **APPROVE** with eight UI tests passed. Focused UI/API/submission verification
  reported 21 passed with two dependency warnings. The timeout fix `4db0ff5` was
  independently approved; its public request ceilings were Core 180s,
  Marketplace 240s and HTTP 270s.

The historical canary proves one payment, delivery and same-order recovery. It
does not prove the new release's external HTTPS reachability, public Agent
identity, new contract deployment acceptance, new wallet authorization, second
wallet isolation, revocation, feedback or final recording.
