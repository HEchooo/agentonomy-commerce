# Agentonomy Commerce 技术演示脚本（不超过 3 分钟）

> **录制前提**：公网钱包购买尚未完成实测。一轮真实闭环录屏可以同时作为验收和视频素材：先完成授权、0.30 TestUSD 购买、交付和同一 `preview_id` 重放，最后才撤销；两份视频可使用同一 `RUN_LABEL`、preview 和交易证据。独立重录需使用新的未授权地址，或先补齐授权续期功能。

完成钱包与设备授权后，这段录屏展示一条真实的 Monad 测试网闭环：外部 Codex 只通过名为
`agentonomy_commerce` 的 MCP server 请求服务，钱包页面完成授权，服务身份由
ERC-8004 校验，Core 约束预算，BudgetExecutor 完成测试网转账，Marketplace
交付 CSV 报表。浏览器只负责钱包登录、签名、有限批准、设备同意和撤销；Codex
不会接触私钥、助记词或钱包签名材料。不要让 Codex 调用本机现有的
`clink_node` MCP server。

画面直接展示运行中的 Codex/MCP 请求和真实交付结果，不用 slides 或代码
walkthrough 代替产品行为。

TestUSD 是测试网测试代币，没有货币价值。本脚本不使用本地模拟成功代替
真实支付；如果录制中的付款或交付处于 pending/unknown，按脚本查询或恢复原订单，
不要新建替代付款。

## 录制前准备

- 只连接名为 `agentonomy_commerce` 的 MCP server。不要调用本机现有的
  `clink_node` MCP server，也不要把两个 server 混用。
- 录制开始前准备好一次真实的有限授权状态：钱包已经登录，画面可显示实际的
  总额、单笔上限、有效期和当前设备同意。示例配置是 1.00 TestUSD 总额、0.50
  单笔上限、一天有效期和 BudgetExecutor 最高 1.00 TestUSD 有限批准；以钱包
  实际返回值为准。新绑定可以剪入一段很短的浏览器片段，但不要声称登录、签名、
  approve 和设备授权都能在 25 秒内完成。撤销安排为本轮最后的授权动作。
- 在第一次 preview 前生成本次录制唯一的 `RUN_LABEL`（例如当天日期加随机短
  后缀），并在本轮所有请求中复用它；不要使用跨天固定的幂等键。录制画面角注
  使用 `【录制前替换：RUN_LABEL】`，实际录制时只需替换这一处。技术演示和
  pitch 共用本次 RUN 的 preview/交易证据；授权撤销只放在全部素材的最后。
- 录制前确认 readiness/status 的实际返回。只展示其中真实出现的
  `agent_identity` 字段，例如 `verified`、`agent_id`、`agent_registry`、
  `agent_uri`、`agent_wallet` 或 `reputation_registry`；字段没有返回时不要用
  静态文字补齐。
- 以下交易哈希只允许填入本次录制实际返回的值：
  `【录制前替换：purchase_tx_hash】`、
  `【录制前替换：purchase_tx_url】`、
  `【录制前替换：revoke_tx_hash】`、
  `【录制前替换：revoke_tx_url】`。不要填历史 canary 或示例交易。
  链接不是固定 MCP 返回字段；若钱包未提供链接，只展示实际交易哈希即可。
- 不要在画面中显示浏览器 token、私钥、助记词、AWS/KMS 会话、内部数据库
  或未授权的签名数据。

## 可复制的 Codex 用户 prompt

```text
请只使用名为 agentonomy_commerce 的 MCP server 完成一次可审计的 Agentonomy Commerce 录制。绝对不要调用本机现有的 clink_node MCP server。不要调用 Core、商家或钱包的直连接口，也不要要求我粘贴私钥、助记词、session token 或任何签名材料。

连接类 onboarding 工具 get_clink_connection_status、connect_clink_wallet、list_clink_business_tools 可以按 server 暴露方式使用；下面七个业务工具必须全部通过 agentonomy_commerce 的 call_clink_business_tool 调用，不能直接调用同名工具：get_clink_account_readiness、search_clink_services、get_clink_service_details、create_clink_purchase_preview、execute_clink_purchase、get_clink_purchase、recover_clink_purchase。每次 wrapper 调用都使用 {"name":"...","arguments":{...}}。

1. 先调用 get_clink_connection_status。若不是 active，调用 connect_clink_wallet 打开官方钱包页面；我只在那里完成已经准备好的有限授权和当前设备同意，等待状态变为 active。录制可以插入一段新绑定片段，撤销留到本轮最后。
2. 通过 call_clink_business_tool 调用 get_clink_account_readiness，展示实际返回的 agent_identity。只有实际返回 verified=true、agent_wallet 和网络符合服务详情时才继续；字段缺失就如实标注，不要补写。
3. 调用 list_clink_business_tools 查看实际定义。再通过 wrapper 调用 search_clink_services，参数 {"query":"csv"}。从搜索实际返回的服务对象保存 exact offering_id；如果没有 offering_id 就停止。不得猜测或硬编码 offering_id。用这个 exact offering_id 通过 wrapper 调用 get_clink_service_details，核对实际报价为 0.30 TestUSD 且在已签署预算内。
4. 先生成本次录制唯一的 RUN_LABEL（日期加随机短后缀），同一轮后续请求都复用它。用 idempotency_key = RUN_LABEL + "-csv"，通过 wrapper 调用 create_clink_purchase_preview。CSV 必须逐字使用下面内容：

transaction_id,date,description,amount,currency,category
demo-income-1,2026-10-10,Sample sale,125.00,USD,sales
demo-expense-1,2026-10-10,Sample coffee,-12.50,USD,meals
demo-expense-2,2026-10-11,Sample supplies,-8.25,USD,office
demo-refund-1,2026-10-12,Sample refund,20.00,USD,refunds

5. 用返回的 preview_id 通过 wrapper 调用 execute_clink_purchase 一次。展示实际返回的 purchase_id、preview_id、state、output_hash、service_result 以及 settlement/receipt 中实际存在的字段；如果返回 transaction_hash、verified、finality 等就展示，缺失字段不要补写。报告应显示 income 145.00 USD、expense 20.75 USD、net 124.25 USD；category totals 保留符号：sales 125.00、meals -12.50、office -8.25、refunds 20.00。
6. 如果付款或交付 pending/unknown，只能通过 wrapper 调用 get_clink_purchase 查询原 purchase_id，或调用 recover_clink_purchase 恢复原订单；不要创建替代 preview、不要再次付款。交付完成后，必须通过 wrapper 对同一个 preview_id 再调用一次 execute_clink_purchase，验证返回的 purchase_id 和 settlement 中实际的交易字段不变；get_clink_purchase 只能用于查询，不能冒充重放。
7. 把撤销放在本轮最后：暂停，等我在官方钱包页面完成 Core 与链上授权撤销。不要把网页 grant revoke 预设为永久 opc_revoke；撤销后先直接调用 get_clink_connection_status，展示实际 status；它可能是 consent_required，也可能是 revoked，不要硬写其中一个。不要再读取 readiness、续期或重连设备。随后只用新的 idempotency_key = RUN_LABEL + "-after-revoke" 通过 wrapper 尝试一次同一 CSV 的新 preview。若 wrapper 在 preview/execute 前返回 status=authorization_required、code=WALLET_AUTHORIZATION_REQUIRED 或其他实际拒绝，原样展示并停止；不要把它描述成链上失败或预算超限。只有实际返回 preview_id 才继续 execute。不要创建新授权或重试付款；不要声称已撤销的 MCP 还能查询旧订单。若要展示旧订单，只能标注为撤销前已返回并保存的记录。
```

## 时间轴、画面和旁白

| 时间 | 画面操作 | 中文旁白 |
| --- | --- | --- |
| 0:00–0:10 | 打开 Codex 对话和服务入口，画面角落标出 `agentonomy_commerce`、`Monad chain 10143`、`TestUSD` 和 `TestUSD 无货币价值`。 | “这是 Agentonomy Commerce 的真实测试网演示。Agent 只通过 `agentonomy_commerce` MCP 请求服务，钱包密钥始终留在用户钱包里。” |
| 0:10–0:25 | 从录制前已经准备好的授权状态开始；展示钱包中的实际总额、单笔上限、有效期和当前设备。可插入一段新绑定片段，但不压缩完整签名流程。回到 Codex，显示 `get_clink_connection_status = active`。 | “这一段从用户已经完成的有限授权开始：画面显示本次实际额度和设备。授权由浏览器完成，Codex 没有私钥。” |
| 0:25–0:40 | 通过 `agentonomy_commerce` 的 `call_clink_business_tool` 调用 `get_clink_account_readiness`，展开实际返回的 `agent_identity` 字段。 | “购买前先验证服务身份。ERC-8004 把实际返回的 `agent_id`、`agent_uri`、`agent_wallet` 和 registry 绑定起来；它证明服务是谁，但不会替用户授予预算。” |
| 0:40–1:00 | 展示 `list_clink_business_tools`，再通过 `call_clink_business_tool` 搜索 `csv`；使用搜索返回的 exact `offering_id` 查看详情、0.30 TestUSD 报价并创建 preview。 | “现在发现 CSV 对账服务，ID 来自搜索结果，不由脚本猜测。报价是 0.30 TestUSD，并在已签署预算内；输入和报价冻结后才进入执行。” |
| 1:00–1:25 | 通过 wrapper 执行购买；展开 delivered 结果和 CSV 汇总，显示 `input_row_count=4`、`unique_transaction_count=4`、`income_totals`、`expense_totals`、`net_totals` 和 `by_category`。显示实际返回的订单、`output_hash` 和 settlement/receipt 字段；将实际交易字段按返回值替换为 `【录制前替换：purchase_tx_hash】` 和 `【录制前替换：purchase_tx_url】`。 | “报告有四行、四个唯一交易：收入 145.00，支出 20.75，净额 124.25 美元。分类保留方向：sales 125.00、meals -12.50、office -8.25、refunds 20.00。这里显示的是本次真实返回的订单、付款和交付证据。” |
| 1:25–1:45 | 对同一个 `preview_id` 通过 wrapper 再调用一次 `execute_clink_purchase`；并排显示第一次和第二次实际返回的 `purchase_id`、settlement 交易字段、状态，以及 readiness 中实际提供的预算信息。不要用 `get_clink_purchase` 代替这次重放。 | “我重放的是同一个 preview，调用的仍是 execute。系统返回原订单和原交易字段，不产生第二笔扣款；查询工具只用于查看订单，不冒充重放。” |
| 1:45–2:25 | 回到官方钱包页面完成 Core 与链上授权撤销；显示实际撤销哈希 `【录制前替换：revoke_tx_hash】` 和 `【录制前替换：revoke_tx_url】`。回到 Codex 读取连接状态，展示实际 `status`（可能是 `consent_required` 或 `revoked`）；用 `RUN_LABEL + "-after-revoke"` 通过 wrapper 尝试一次新 preview，展示实际授权拒绝。 | “最后撤销授权。撤销后，新的购买会被拒绝；撤销前的订单响应作为留存记录保存，访问权限和数据留存分别处理。” |
| 2:25–2:55 | 画面停在撤销前已保存的订单结果、撤销状态和拒绝结果；遮挡所有密钥与会话材料。 | “这条链路展示的是测试网真实转账和独立结算证据，TestUSD 没有货币价值。视频中的交易哈希均来自本次录制。” |

## 录制时必须保留的结果

画面应直接保留 MCP 实际返回的 `agent_identity`、`offering_id`、`preview_id`、
`purchase_id`、`state`、`input_hash`、`output_hash`、`settlement`、receipt 和
报表汇总；settlement 中若有 `transaction_hash`、`verified`、`status`、finality
等字段则展示。重放必须再次调用同一个 `preview_id` 的
`execute_clink_purchase`，并显示实际返回的订单/交易字段；撤销后的新尝试要保留
原始拒绝 `status/reason`，例如实际返回的 `authorization_required` 和
`WALLET_AUTHORIZATION_REQUIRED`。旧订单如需出镜，必须标注为撤销前已返回并保存的记录，
不能暗示已撤销的 MCP 仍能访问它。任何字段缺失、身份未验证、付款状态未知或撤销
结果未完成时，暂停录制并处理原状态；不要剪辑出一个看似成功的替代流程。
