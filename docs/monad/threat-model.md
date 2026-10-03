# Monad 预算支付轨道：威胁模型

状态：设计与本地实现审查稿，已执行分工代码复核；公共测试网验收和第三方安全审计尚未完成。本文描述控制边界和剩余风险，不是安全审计意见。

## 保护对象与信任边界

| 对象/边界 | 负责内容 | 不应被信任的输入或结论 |
| --- | --- | --- |
| Core | 钱包身份、既有 EIP-191 Spending Grant、业务 scope、policy/risk、预算预占、审计和恢复 | Agent 自己提供的 owner、grant、签名者、付款证明 |
| 用户钱包 | 对 grant 的 EIP-712 授权、撤销和有限 allowance | 对话确认不能替代有效签名或已撤销授权 |
| `AgentonomyBudgetExecutor` | 固定代币/收款方、有效期、单笔/总额、双签、撤销、防重放和原子 ERC-20 付款 | 前端、MCP 或商家不能扩大链上授权 |
| execution signer | 在 Core 重新授权后签署一笔固定订单的 `PurchaseExecution` | 其签名不能改变 grant、订单、报价或收款方 |
| relayer | 只支付 gas 并转发完整 calldata | relayer 不能改变付款字段；广播回执本身也不是结算证明 |
| Watcher / 双 RPC | 独立核对链、交易、receipt、日志、canonical block 和 Monad Verified 边界 | 单个 RPC、单个 receipt、商家声称或 tx hash |
| 第一方商家 | 校验 Core 收据、订单、输入哈希并交付固定 CSV 结果 | 商家没有预算 authority，也不能自行标记链上付款成功 |
| 持久化 journal | 保存不可变的订单、签名交易和恢复信息 | journal 不能取代 Core 账本，也不能在未知状态下发新付款 |

Core 与合约共同形成控制面：Core 决定业务是否可买，合约把用户签名的硬上限落实到代币转账。Marketplace 只能提交已冻结报价和输入，不能创建身份、改预算或选择任意商家地址。

## 主要威胁与控制

| 威胁 | 控制 | 剩余风险/限制 |
| --- | --- | --- |
| 伪造或篡改 grant | EIP-712 域绑定 `chainId` 和 `verifyingContract`；owner 签名恢复必须匹配 grant owner；字段和 uint256 规范化 | MVP 只支持 EOA；用户若在钱包中批准过大的 allowance，合约不会替用户收回 allowance |
| 改 token、payee、scope、金额或报价 | grant 绑定固定 token/payee/scope；execution 绑定完整 grant digest、purchase ID、quote hash 和 amount；Core 与 watcher 双重比较 | 业务规则仍需在 Core 实现；链上硬上限不等于完整风险判断 |
| 跨链或跨合约重放 | EIP-712 域、不可变链 ID、合约地址和交易 calldata 均核验 | 迁移到新合约必须重新授权；旧 grant 不会自动迁移 |
| 同订单重复支付或换 grant 重放 | `paid[owner][purchaseId]` 跨 grant 防重放；`grantDigests[owner][grantId]` 首次使用后固定正文；Core 保持稳定 purchase ID | 订单 ID 的生成和 Core 持久化仍是系统责任，不能让 Agent 自选替换 |
| 撤销竞态 | owner 可直接按 `(owner, grantId)` 调用 `revoke`；最终结果按链上排序解释，不承诺取消已排序的付款 | RPC 延迟、用户未及时撤销和已进入区块的付款仍有经济风险 |
| 恶意或非标准 ERC-20 | 只接受部署时核验的测试资产；要求 `transferFrom` 返回恰好 32 字节的 `true`，并核对 owner 扣款和 payee 增款；效果先写入、失败整体回滚、非重入 | 余额检查不能让任意恶意 token 变可信；首版不支持 rebasing、fee-on-transfer 或未核验的公共资产 |
| relayer 改交易或利用 gas 配置 | Backend 在签名前固定 chain、nonce、to、value、data、gas 上限和 gas price；广播前后校验签名者、交易哈希和返回哈希 | relayer 可拒绝服务；密钥基础设施和主机隔离仍需部署方负责 |
| 伪造 receipt、日志或重组后旧块 | watcher 比较两份独立 RPC；要求成功 receipt、相同 canonical block、完整 calldata、唯一 `PaymentExecuted` 和 `Transfer`，并要求显式最终性 | 两个 RPC 若共享故障域，独立性会下降；Monad 规则/客户端升级后必须重新核对 |
| 把 `finalized` 或确认数误当作 Monad Verified | 公共模式只接受 `finality.kind=monad_verified`；当前实现按“finalized 高度减 3”计算 Verified 边界，并记录边界块哈希 | 该规则是版本化的网络事实，不是永久常数；未重新核对前应 fail closed |
| 进程崩溃后重复广播 | 广播前持久化 raw transaction、tx hash、nonce 和 execution；未知状态保留 reservation，恢复同一 attempt，不创建第二笔付款 | journal 损坏、数据库损坏或 RPC 不一致会阻止恢复，需要人工检查 |
| 付款成功但商家交付失败 | 进入 `paid_delivery_pending`，只重试同一订单的交付；已付款订单的恢复不能再次扣款；商家按 purchase ID 和 input hash 幂等 | 商家长期不可用会延迟交付；结果保留期和数据删除策略仍是部署选择 |
| 风险 provider 被伪造或绕过 | 测试网只启用明确的第一方 allowlist（merchant/resource/token/payee/network）；没有 Monad 覆盖时拒绝常规付款，不伪造第三方风险结果 | 这是测试网范围的替代策略，不是生产风险服务；不得扩大为通用低风险结论 |
| 误把本地模拟当真实支付 | 本地模式显式 `local_anvil`、`TestUSD`、`real_funds=false`；真实链失败不得回退模拟成功；公开材料必须单独列交易证据 | 录屏、截图或浏览器文案仍可能造成误解，需要在开场和画面上重复边界 |
| 泄露私钥、执行凭据或原始 CSV | 密钥不进源码、manifest、MCP 响应或链上数据；本地测试 key 只在进程中生成；链上只承诺最小摘要 | 日志、core dump、RPC provider 或部署主机仍可能暴露敏感数据，需由部署方管理 |

## 订单状态与异常处理

- `quoted -> reserved -> broadcast_pending`：广播前先记录不可变的 purchase、quote、grant digest 和 attempt。报价、scope、代币或 payee 漂移时拒绝执行。
- `broadcast_pending -> payment_verified`：只有双 RPC 观察、成功 receipt、正确事件和 Monad Verified 边界全部满足时才结算；缺 receipt、超时、RPC 分歧或未知状态都保持 pending。
- `payment_verified -> delivered`：商家只接收已验证 Core receipt 和相同输入哈希。交付失败进入 `paid_delivery_pending`，只能重新交付。
- `broadcast_pending -> unpaid_terminal`：只有独立证据证明交易在最终化后 revert，才允许释放 reservation；网络超时或“没有找到交易”不是未付款证明。

恢复动作必须以原 purchase/attempt 为键。任何人遇到未知状态都应先查原 tx，再决定是否需要人工处理；不能新建订单来掩盖不确定的付款。

## 已覆盖的本地负面场景

协议、合约和 watcher 测试覆盖了错域/错链、伪签名、过期、未生效、超单笔、超总额、换 token/payee、撤销、重复 purchase、grant 冲突、异常 ERC-20 返回、重入、receipt/log/canonical block 不一致、双 RPC 分歧和交付重放等场景。完整结果与剩余项见 `acceptance.md`。本地环境允许 loopback 后可执行真实 EVM 测试；这些结果不能写成公共验收。

## 明确的剩余风险与非目标

首版不提供 EIP-1271、智能钱包、7702、任意合约调用、升级代理、批量付款、退款仲裁、多链路由、主网资金或隐私保证。公共测试资产地址、部署地址、外部钱包、签名服务、商家公网部署和安全审计均待用户/部署方确认。自部署 `TestUSD` 不是官方 USDC；本地签名、receipt 和商家结果也不是 Monad 采用证据。


## 广播前崩溃的保守边界

已持久化但尚未领取广播的 `pending/ready` attempt 可在重新检查当前授权、policy 和 allowance 后继续发送同一签名交易。已经提交 `broadcasting` claim 而缺少回执时，无法证明网络调用是否发生；保持原预算、仅复验同一交易，不自动重发或生成替代交易。这类极窄窗口可能需要人工核查，不能承诺任何故障都自动恢复。

本地浏览器将当前报价引用、确认状态与输入保存在当前标签页的 sessionStorage，便于刷新恢复；原始输入不写入链上或源码。公开部署需要另行确定数据保留与钱包会话方案。
