# Agentonomy Commerce 产品 pitch 脚本（不超过 2 分钟）

> **录制前提**：公网钱包购买尚未完成实测。一轮真实闭环录屏可以同时作为验收和视频素材：先完成授权、0.30 TestUSD 购买、交付和同一 `preview_id` 重放，最后才撤销；两份视频可使用同一 `RUN_LABEL`、preview 和交易证据。独立重录需使用新的未授权地址，或先补齐授权续期功能。

这是一段面向评审者的产品演示，旁白全中文，画面使用正在运行的 Codex、
钱包页面和已交付报表。它不使用 slides 或代码 walkthrough 代替产品行为。
浏览器只用于钱包登录、签名、有限批准、设备同意和撤销；服务发现、preview、
execute 和结果展示都在 MCP 工作流内。
画面保留 `Monad testnet` 标记，并在画面出现一次这句话：TestUSD 是测试网测试代币，没有货币价值。

## 可复制的 Codex 用户 prompt

```text
请只使用名为 agentonomy_commerce 的 MCP server 演示一次 Agentonomy Commerce 购买；绝对不要调用本机现有的 clink_node MCP server。不要调用 Core、商家或钱包的直连接口，不要索要私钥、助记词或 session token。

录制从已经准备好的有限钱包授权状态开始，先用 get_clink_connection_status 确认 active，并在画面显示实际额度和当前设备；若不是 active，调用 connect_clink_wallet 打开官方钱包页面，完成授权后再继续。下面七个业务工具必须全部通过 agentonomy_commerce 的 call_clink_business_tool 调用：get_clink_account_readiness、search_clink_services、get_clink_service_details、create_clink_purchase_preview、execute_clink_purchase、get_clink_purchase、recover_clink_purchase；每次使用 {"name":"...","arguments":{...}}。不要直接调用同名业务工具。

通过 wrapper 调用 get_clink_account_readiness，展示实际返回的 agent_identity；再调用 search_clink_services，参数 {"query":"csv"}，只使用搜索结果中的 exact offering_id 查看详情，禁止猜测或硬编码 offering_id。只口述实际出现的 `verified`、`agent_id`、`agent_registry`、`agent_uri`、`agent_wallet` 或 `reputation_registry` 字段。核对实际 0.30 TestUSD 报价后，生成本次录制唯一的 RUN_LABEL，用 RUN_LABEL + "-csv" 创建 preview 并执行下面的四行 CSV。返回实际订单、settlement/receipt、output_hash 和交付报告；报告应显示 income 145.00 USD、expense 20.75 USD、net 124.25 USD，分类为 sales 125.00、meals -12.50、office -8.25、refunds 20.00。

对同一个 preview_id 必须再次通过 wrapper 调用 execute_clink_purchase，证明订单和实际交易字段不变且没有二次扣款；get_clink_purchase 只能查询，不能冒充重放。把撤销放在最后：等我在钱包页面撤销授权，不要把网页 grant revoke 预设为永久 opc_revoke；先读取 get_clink_connection_status，展示实际 status（可能是 consent_required 或 revoked），再用 RUN_LABEL + "-after-revoke" 通过 wrapper 尝试一次新的 preview。若在 preview/execute 前返回 status=authorization_required、code=WALLET_AUTHORIZATION_REQUIRED 或其他实际拒绝，原样展示并停止；不要把它描述成链上失败或预算超限。不要续期、重连或创建新授权；不要声称已撤销的 MCP 还能查询旧订单。只展示实际返回字段，缺失字段不要补写；pending/unknown 时查询或恢复原订单，不创建替代付款。

transaction_id,date,description,amount,currency,category
demo-income-1,2026-10-10,Sample sale,125.00,USD,sales
demo-expense-1,2026-10-10,Sample coffee,-12.50,USD,meals
demo-expense-2,2026-10-11,Sample supplies,-8.25,USD,office
demo-refund-1,2026-10-12,Sample refund,20.00,USD,refunds
```

## 时间轴、画面和旁白

| 时间 | 画面操作 | 中文旁白 |
| --- | --- | --- |
| 0:00–0:16 | 画面显示真实 Codex 对话、团队名 Agentonomy、目标客户“开发 Agent 的团队”和 `Monad testnet` 标记；不显示任何密钥。 | “我们是 Agentonomy，目标客户是开发 Agent 的团队。我们要解决的是 Agent 需要付钱买结果，而用户仍要掌握资金边界和可核验的交付证据。” |
| 0:16–0:32 | 展示从已准备好的钱包授权状态开始的实际总额、单笔上限、有效期和当前设备；回到 Codex 显示 `active`。 | “Agent 的购买不能以交出钱包钥匙为前提。用户先给出有限预算并同意设备，Agent 通过受限 MCP 能力行动。” |
| 0:32–0:52 | 在 Codex 展开实际 readiness 的 `agent_identity`，再展示服务搜索返回的 exact `offering_id`、服务详情和 0.30 TestUSD 报价。 | “方案把三件事连起来：用户控制的预算、可核验的服务身份，以及可复验的订单结果。ERC-8004 说明服务是谁，Core 负责买方身份、策略和预算。” |
| 0:52–1:15 | 展示真实 preview、execute 和交付报告；显示四行数据的收入 145.00、支出 20.75、净额 124.25 USD，以及实际返回的订单、settlement/receipt 和 `output_hash`。画面只显示实际返回字段；若有交易哈希和链接，分别填入 `【录制前替换：purchase_tx_hash】`、`【录制前替换：purchase_tx_url】`。 | “这是正在运行的产品证据：Agent 购买一份虚构 CSV 报表，四行输入得到清晰的收入、支出、净额和分类结果。订单、付款和交付摘要都来自这次调用。” |
| 1:15–1:32 | 对同一个 `preview_id` 再次调用 `execute_clink_purchase`，并排显示相同订单/交易字段；随后完成最后的授权撤销，展示实际撤销链接 `【录制前替换：revoke_tx_hash】`、`【录制前替换：revoke_tx_url】`，以及新 preview 的实际授权拒绝。 | “同一个 preview 再执行不会产生第二笔扣款。用户撤销后，新的购买被拒绝；撤销前的订单响应作为留存记录保存，访问权限和数据留存分别处理。” |
| 1:32–1:47 | 撤销后不再调用 MCP；只回看撤销前已保存的 settlement/receipt 可核验字段和 `Monad testnet` 标签。 | “Monad 的 EVM 环境让服务身份、签名预算和支付合约能够在同一条链上协作。这里展示一次真实测试网交易。” |
| 1:47–2:00 | 不再调用 MCP；画面回到撤销前已保存的产品结果、身份和预算边界，出现下一步产品愿景关键词：更多服务、结果恢复、透明收据、用户自主管理限额。 | “下一步是让更多 Agent 服务接入同一套身份、预算和收据协议，把恢复交付、透明结算和用户自主管理限额做成可复用的产品基础设施。” |

## 录制前替换与事实边界

- 录制前生成唯一的 `RUN_LABEL`，画面角注使用
  `【录制前替换：RUN_LABEL】`，本轮固定复用它；不要使用跨天固定的幂等键。
  只替换本次录制实际产生的 `【录制前替换：purchase_tx_hash】`、
  `【录制前替换：purchase_tx_url】`、`【录制前替换：revoke_tx_hash】`、
  `【录制前替换：revoke_tx_url】`，不要填历史 canary、示例或预先写好的哈希。
  链接不是固定 MCP 返回字段；若钱包未提供链接，只展示实际交易哈希即可。
- `preview_id`、`purchase_id`、`output_hash`、身份字段、settlement/receipt 和
  拒绝原因由 MCP/钱包画面直接展示。若 settlement 实际返回
  `transaction_hash`、`verified`、`status` 或 finality 等字段就展示；若身份不是
  `verified`，不要用静态文字补齐。
- 旧订单如需出镜，必须标注为撤销前 MCP 响应已返回并保存的数据；撤销后不要用
  已撤销的 MCP 再次访问它。
- 若支付、交付或撤销仍为 pending/unknown，保留原状态并暂停录制；不要重放
  新订单或切换到模拟结算。
