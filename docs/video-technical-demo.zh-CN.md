# Agentonomy Commerce 技术演示脚本（不超过 3 分钟）

> **录制状态**：公网版本已部署，当前发布版本的健康检查和配置预检已通过；这
> 仍不是已完成验收的证明。公网钱包购买、交付、同 preview 重放、用户反馈签名
> 和撤销拒绝必须在本次录制中重新取得实际返回值，不能旁白成已在线成功。

目标镜头是一条 Monad 测试网闭环：外部 Codex 只通过
agentonomy_commerce MCP server 请求服务；钱包页面处理身份、有限预算、
allowance、设备同意和反馈签名；Core 约束预算，BudgetExecutor 执行
TestUSD 转账，Marketplace 交付 CSV 报表。Codex 不接触私钥、助记词、浏览器
token、AWS/KMS 会话或原始签名材料。不要让 Codex 调用本机现有的 clink_node
MCP server。

TestUSD 是没有货币价值的测试资产。本脚本不把本地模拟或历史 canary 当作本次
真实支付；付款、交付、反馈或撤销处于 pending/unknown 时，只查询或恢复原记录，
不创建替代付款。

## 录制前准备

- 只连接名为 agentonomy_commerce 的 MCP server。录制前核对实际连接状态、
  钱包、设备、预算、allowance 和服务身份；不要用静态文字补齐缺失字段。
- 2026-10-13 的公网预检确认匿名状态接口拒绝、绑定和反馈脚本与已测试源码一致、
  两条 RPC 的 chain 10143 部署与身份核对，以及隔离签名器的两次 KMS DryRun。
  独立 CommerceOpcClient 已列出 11 个业务工具；搜索、详情和服务历史查询成功，
  购买前的已观察交付次数和买家评分样本均为 0，买家平均评分为空。
- 仅报价的预检已返回 0.30 TestUSD，payment_capability.auto_pay_compatible 为 true；
  随后 readiness 为 ready，已用 0.00、预占 0.00、剩余 1.00。该预检没有 execute、
  KMS 实际签名或广播。不要执行这个预检 preview；录制时使用新的 RUN_LABEL
  和本次真实返回的 preview_id。
- 若当前 Codex MCP 窗口显示 Transport closed，先在 Settings → MCP servers →
  Restart 后重查连接。独立进程成功不等于当前录制窗口已经恢复连接。
- 本轮预检时设备／授权到北京时间 2026-10-13 11:53:18，KMS 临时会话到
  12:24:34；录制前重新读取实际 expires_at，不要照搬这些时效。
- 当前返回中的 legacy settlement_submissions 是 relayer 的 pending nonce，
  不是本次购买次数。重放证据比较原 purchase、交易 hash 和余额，不据此推算扣款。
- 当前演示配置是 1.00 TestUSD 总额、0.50 单笔上限、一天有效期和 1.00 的
  有限 allowance；录制画面以本次实际返回为准。授权撤销留到全部购买、重放、
  反馈和历史镜头完成之后。
- 如果开录前状态是 expired 或 revoked，先走新的授权流程：在官方同源钱包页
  生成新的 grant、签新的 budget、重新完成设备 consent。旧 grant、旧订单和
  旧历史保留；不要重新领取 faucet，也不要删除状态。若存在 pending/unknown
  资金动作，先 recover 原 purchase，再开始新授权。
- 第一次 preview 前生成本轮唯一的 RUN_LABEL（日期加随机后缀），所有请求
  复用它；新尝试使用 RUN_LABEL-after-revoke。以下字段只填本次真实返回值：
  purchase_id、purchase_tx_hash、feedback_score、feedback_tx_hash、
  revoke_tx_hash、history_sample_size、history_mean_score 和
  history_freshness。
- 录制不展示私钥、助记词、浏览器 token、OPC proof、AWS/KMS 会话、内部数据库
  或 raw signed payload。交易链接不是固定返回字段；没有实际 URL 时只显示实际
  hash。

## MCP 工具路由和实际 schema

get_clink_connection_status、connect_clink_wallet 和 list_clink_business_tools
按 server 实际暴露方式使用。下面 11 个业务工具全部必须通过
agentonomy_commerce.call_clink_business_tool，参数外层固定为
{"name":"工具名","arguments":{...}}；不能直接调用同名工具或 Core、商家、钱包
接口。

| 工具 | arguments 合同 |
| --- | --- |
| get_clink_account_readiness | {} |
| search_clink_services | query 为 string，最多 200 字符 |
| get_clink_service_details | 必填 offering_id |
| create_clink_purchase_preview | 必填 offering_id、csv_text、idempotency_key；csv_text 最多 131072 字符 |
| execute_clink_purchase | 必填 preview_id |
| get_clink_purchase | 必填 purchase_id |
| recover_clink_purchase | 必填 purchase_id；csv_text 可选，非空且最多 131072 字符 |
| prepare_clink_purchase_feedback | 必填 purchase_id 和整数 score，0 <= score <= 100；只准备，不发送交易 |
| get_clink_purchase_feedback | 必填 purchase_id；独立读取原反馈和当前撤销状态 |
| verify_clink_purchase_feedback | 必填 purchase_id 和匹配 ^0x[0-9a-fA-F]{64}$ 的 transaction_hash；只复验，不签名发送 |
| get_clink_service_history | 必填 offering_id；返回成交与评分分离的公开历史、样本数和 freshness |

## 可复制的 Codex 用户 prompt

~~~text
请只使用名为 agentonomy_commerce 的 MCP server 完成一次可审计的 Agentonomy
Commerce 录制。不要调用 clink_node MCP server，不要直连接 Core、商家或钱包，
不要要求我粘贴私钥、助记词、session token 或任何签名材料。

先调用 get_clink_connection_status；若不是 active，通过 connect_clink_wallet
打开官方钱包页面。我只在那里完成已经准备好的有限授权和当前设备 consent。
若状态是 expired 或 revoked，先完成新的 grant、budget 签名和设备 consent；
保留旧记录，不领取 faucet。若已有 pending/unknown 付款，先 recover 原
purchase，不能开新付款。

调用 list_clink_business_tools 查看实际定义。此后 11 个业务工具都通过
call_clink_business_tool，外层参数为 {"name":"...","arguments":{...}}。
调用 get_clink_account_readiness，只有实际返回的 agent_identity 已验证且与
服务详情一致才继续；缺字段就停止并如实展示。

通过 wrapper 调用 search_clink_services，参数 {"query":"csv"}，保存搜索结果
中的 exact offering_id；没有 offering_id 就停止。用它调用
get_clink_service_details，再调用 get_clink_service_history 展示购买前的实际
历史。如果历史为空，显示实际的空结果，不补写评分、样本数或平均分。

生成本轮唯一 RUN_LABEL，后续 idempotency_key 使用 RUN_LABEL + "-csv"。用 exact
offering_id 调用 create_clink_purchase_preview。CSV 必须逐字使用：

CSV:
transaction_id,date,description,amount,currency,category
demo-income-1,2026-10-10,Sample sale,125.00,USD,sales
demo-expense-1,2026-10-10,Sample coffee,-12.50,USD,meals
demo-expense-2,2026-10-11,Sample supplies,-8.25,USD,office
demo-refund-1,2026-10-12,Sample refund,20.00,USD,refunds

用返回的 preview_id 通过 wrapper 调用 execute_clink_purchase 一次。只展示实际
返回的 purchase_id、state、output_hash、service_result 和 settlement/receipt
字段；如果有 transaction_hash、verified 或 finality 就展示，否则不要补写。
输入对应的报告应为 income 145.00 USD、expense 20.75 USD、net 124.25 USD，
分类保留 sales 125.00、meals -12.50、office -8.25、refunds 20.00；实际返回
不一致就停止。

如果付款或交付 pending/unknown，只能用 get_clink_purchase 查询原订单或用
recover_clink_purchase 恢复原订单。交付完成后，再把同一个 preview_id 通过
wrapper 调用 execute_clink_purchase 一次；比较两次实际返回的 purchase_id、
交易 hash、state 和 output_hash，确认原交易不变、没有第二笔付款。

购买和重放证据保存后，询问我一个真实的 0 到 100 整数评分；绝不代我猜分。
用原 purchase_id 调用 prepare_clink_purchase_feedback，打开它返回的同源反馈
页面。我在自己的钱包页查看披露内容并签名发送反馈。拿到实际 transaction_hash
后，用 verify_clink_purchase_feedback 通过 wrapper 复验；再用
get_clink_purchase_feedback 独立回查原 purchase_id 和 original payment/output
hash。没有实际 hash 或反馈仍 pending 时，保留原状态，不重发。

随后用 exact offering_id 调用 get_clink_service_history，展示实际的已交付成交、
评分分离、sample size、mean score 和 freshness。没有已验证反馈时只展示实际的
无评分状态，不制造平均分或评价。

把撤销放在本轮最后。我在官方钱包页面完成 Core 与链上授权撤销；只有实际返回
时才显示 revoke transaction hash。回到 Codex 后先调用
get_clink_connection_status，原样展示实际 status。用新的
RUN_LABEL + "-after-revoke" 通过 wrapper 尝试同一 CSV 的新 preview；如果返回
authorization_required、WALLET_AUTHORIZATION_REQUIRED 或其他实际拒绝，原样
展示并停止，不再 execute、续期或重连。不要声称撤销后还能读取旧订单；旧订单
只能标注为撤销前已经返回并保存的记录。
~~~

## 时间轴、画面和旁白

| 时间 | 画面操作 | 中文旁白 |
| --- | --- | --- |
| 0:00–0:18 | 打开 Codex 和 agentonomy_commerce MCP，展示实际连接状态、Monad chain 10143、TestUSD 无货币价值。 | “这是 Agentonomy Commerce 的 Monad 测试网演示。Agent 通过 MCP 请求服务，钱包密钥始终留在用户钱包里。” |
| 0:18–0:38 | 展示实际总额、单笔上限、有效期、allowance、设备同意和 agent_identity。 | “用户设定有限预算和设备范围。购买前，系统还要确认实际服务身份；ERC-8004 只说明服务是谁，不授予预算。” |
| 0:38–0:58 | 搜索 csv，显示 exact offering_id、服务详情和购买前 get_clink_service_history 返回的实际样本/评分状态。 | “服务 ID 来自搜索结果。先看已经验证的服务历史；成交记录和买家评分是分开的，没有历史就明确显示为空。” |
| 0:58–1:25 | 创建 preview，显示冻结的 0.30 TestUSD 报价和四行 CSV；执行一次，展开实际订单、支付证据、交付报告和 hash。 | “报价和输入先冻结，再由 Core 在授权范围内执行。这里只显示本次返回的支付和交付证据。” |
| 1:25–1:45 | 对同一个 preview_id 再调用 execute，比较两次的 purchase、交易字段、state 和 output_hash。 | “这是同一 preview 的重放。系统返回原订单和原交易，不产生第二笔扣款。” |
| 1:45–2:20 | 询问真实整数评分；调用 prepare，打开同源反馈页，由买家钱包查看披露内容并签名；用 verify 和 get 回查实际 original hash。 | “评分必须来自买家本人。准备工具不发送交易，钱包页面由用户签名；服务端随后独立复验原支付和结果绑定。” |
| 2:20–2:38 | 调用 get_clink_service_history，显示真实 sample size、mean score、freshness；没有反馈就显示实际无评分。 | “历史只增加已验证的数据。当前只有一个 pilot 服务，不把一次评分包装成成熟的多商家市场。” |
| 2:38–2:58 | 最后在钱包页面撤销授权；展示实际 revoke hash（若有）、状态和新 preview 的实际拒绝。 | “撤销后新的购买被授权边界拒绝。旧订单仍是撤销前的记录，不能变成新的权限。” |

## 录制时必须保留的结果

保留 MCP 实际返回的 agent_identity、offering_id、购买前和购买后的
service_history、preview_id、purchase_id、state、input_hash、output_hash、
settlement、receipt、feedback transaction hash 和拒绝结果。反馈分数、样本数、
平均分、时间和交易 hash 必须来自本次 run；不允许用历史 canary、示例值或旁白
猜测替代。

## 失败处理

- 身份、网络、payee、报价、付款或交付证据缺失/矛盾时暂停，不剪辑出替代成功。
- 付款结果 unknown 只恢复原 purchase；反馈结果 pending 只复验原 feedback hash，
  不能重发评分或创建第二个支付。
- grant/consent 过期或撤销时，走新 grant、新 budget 签名和新设备 consent；保留
  旧订单和历史，不重新领取 faucet。结算中的原资金动作先 recover，再开始新流程。
- 撤销后的新 preview 若没有返回授权拒绝而意外出现 preview_id，停止录制并保存
  原始响应；不要继续 execute。
