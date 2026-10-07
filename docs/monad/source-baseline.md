# Monad 预算支付轨道：来源与基线

状态（截至 2026-10-07）：本地 Anvil/EVM、loopback HTTP、统一 MCP 和浏览器 UI 验收已完成；一笔 Monad 测试网 10143 的 0.30 TestUSD 付款已通过双 RPC 验证并交付，原订单 API recovery 和同单 query replay 已记录在 [`role-session-acceptance.md`](role-session-acceptance.md)，预算 remaining 为 0.70 TestUSD。本文保留 2026-10-03 的来源基线，不是部署证明、审计报告或提交资格结论；operator UI/API 与 merchant 仍是 loopback，public review service 仍是模拟。

## 来源快照

| 项目 | 记录 |
| --- | --- |
| 目标工作树 | `/private/tmp/agentonomy-commerce-monad-20261003` |
| 分支 | `codex/monad-budget-commerce` |
| 实施基线 | `029cd0b` |
| Clink 本地参考与目标比较 | 本地 Clink HEAD `bae9258` 对比目标工作树基线 `029cd0b` |
| 本地 `origin/dev` 引用 | `042e5008`；该引用未重新 fetch，不称为远端最新 |
| 原始导出 | Clink working tree 的 Commerce 定向导出；`docs/source-manifest.json` 记录的导出日期为 2026-09-22 |

本分支只选择性复用 Clink 的 Core、Marketplace、Node、Hosted 与现有测试代码，保持 `clink_node` MCP 入口和原有模块边界。导出结果是 standalone source tree，运行时不依赖同级或外部 Clink 工作区；来源关系只用于代码 provenance。它没有把另一个 Clink 工作区整体覆盖进来；Clink 参考工作区本身也没有被本任务修改。本次实现保存在 `codex/monad-budget-commerce` 分支；最终提交材料必须绑定到实际发布的 Git commit，不能仅引用目录快照。

## 已确认的实现边界

本轮新增的是一条普通 ERC-20 预算支付轨道：

- `AgentonomyBudgetExecutor` 使用 EIP-712 `SpendGrant` 和 `PurchaseExecution`。合约固定代币、收款方、单笔上限、总上限、有效期和执行签名者；支持用户撤销、按 owner 的订单防重放和不可升级的原子 `transferFrom`。
- Core 仍控制钱包身份、既有 EIP-191 Spending Grant、policy/risk、预算预占、执行复验和审计。链上 grant 是额外硬上限，不是第二个 Core 账本。
- Python 协议把所有 uint256 以规范无符号十进制字符串写入 JSON；链上金额使用代币最小单位。当前首版只支持 EOA 签名，不支持 EIP-1271 或 7702。
- `BudgetBackend` 在广播前保存原始签名交易、交易哈希、nonce 和执行摘要；缺少回执时保留未知状态，不生成第二笔经济付款。
- `budget_watcher` 对本地和公共模式都需要两份独立 RPC 观察；每份观察都必须核对链 ID、交易 calldata、成功回执、与回执一致的 canonical block、`PaymentExecuted`、ERC-20 `Transfer` 以及显式的已验证最终性边界。两份观察必须同意同一个最终性边界块。Monad 公共模式要求 `monad_verified`；本地 Anvil 使用明确标记的 `local`。
- `examples/monad_commerce` 将 Core 和 Marketplace 放在进程隔离的本地组合中，商家通过真实 loopback HTTP 交付 CSV 结果。这个组合仍是本地验收工具，不是公网服务。

设计正文和 wire schema 见 [`design.md`](design.md) 与 [`grant.schema.json`](grant.schema.json)。

## 网络与资产事实

Monad 测试网的只读核查记录为 chain ID `0x279f`（十进制 `10143`）。本次使用的官方资料入口是 [Monad testnet 文档](https://docs.monad.xyz/developer-essentials/testnet) 和 [Monad block states 文档](https://docs.monad.xyz/monad-arch/consensus/block-states)。已知 RPC 入口为 `https://testnet-rpc.monad.xyz` 与 `https://rpc-testnet.monadinfra.com`；2026-10-03 的核查能读取 `finalized` 但没有广播交易，这是本基线的历史预检。当前部署和付款证据见 [`role-session-acceptance.md`](role-session-acceptance.md)。

本基线创建时，公共测试网的目标代币、执行合约和收款方地址尚未确定；当前部署地址、代码哈希、付款交易和双 RPC 证据见 [`role-session-acceptance.md`](role-session-acceptance.md)。商家公网地址仍未提供，当前 merchant 只经 loopback HTTP 运行。仓库中的 `AgentonomyTestUSD`/`BudgetTestUSD` 是测试资产；自部署 `TestUSD` 不能称为官方 USDC。任何地址、字节码哈希、余额、交易和部署状态都必须从当前独立证据读取，不能从本地测试 fixture 推断。

## 验证记录

当前本地 EVM composition 已通过真实合约执行、Core/Marketplace/watcher、loopback HTTP merchant delivery/replay、统一 `clink_node` MCP 和浏览器 UI 路径；结果中的 `settlement_mode` 为 `local_anvil`，不代表公共链。合约命令统一从仓库根目录使用 `forge build --root contracts`、`forge fmt --root contracts --check` 和 `forge test --root contracts`。

2026-10-03 基线全回归记录在 [`acceptance.md`](acceptance.md)；2026-10-07 的最新相关回归及公共测试网证据记录在 [`role-session-acceptance.md`](role-session-acceptance.md)。本材料不把历史计数冒充最新结果，也不把局部通过写成全仓验收通过。

## 公共验收后仍需补齐或持续核对

1. 持续从 [`role-session-acceptance.md`](role-session-acceptance.md) 核对 Monad 测试网代币、六位精度、代码哈希、执行合约、收款方和两个独立 HTTPS RPC；网络或代码变更后重新读取。
2. owner wallet、execution signer、relayer/gas signer、owner grant、有限 allowance 和一笔 canary 付款已完成一次受控验收；用户仍需完成链上 revoke 并记录新付款被拒绝的证据。私钥始终留在用户控制的钱包或受控签名器中。
3. 同一交易在两条 RPC 上的 receipt、canonical block、`Verified` 边界、`PaymentExecuted`/`Transfer` 日志、Core 预算前后值和 loopback merchant 交付结果已记录在 [`role-session-acceptance.md`](role-session-acceptance.md)。
4. 在网络变更、工具升级或 Monad 最终性规则变化后重新核对 `finalized - 3` 的适用性。
5. 用户已授权可提交的第一方代码（不包括私钥和凭据），团队身份 Agentonomy 与联系邮箱已记录；剩余许可事项只有是否采用统一 OSI 许可证的实际选择。现有 Logo 仅按源文件 provenance 记录，不在此声明其许可。

当前状态可以写成“已完成一笔受双 RPC 复验的 Monad testnet canary，并完成同单恢复/交付”；仍不能写成“公网商家/API 已上线”“已审计”或“已被采用”。本地 EVM 通过和 loopback merchant 交付仍不能替代公网服务证据。

## 本次复用与同步取舍

| 能力 | 实施决定 |
| --- | --- |
| Core 钱包身份、EIP-191 Spending Grant、Action/Policy、预算预占、账本、Audit、交付 finalize | 复用本仓库已有源码；新增 Budget 子类固定选路，既有生产网络 allowlist 不扩大 |
| Marketplace 冻结输入/报价、稳定 purchase、HTTP 商家交付和结果存储 | 复用本仓库版本；复制 composition 的必要装配到 `examples/monad_commerce` |
| 统一 Node MCP、工具契约与客户端身份边界 | 复用本仓库 Node 包，无外部 Clink 运行时依赖 |
| Clink 本地最新 C standing authorization（`8657964`、`6027f04`、`4f9f56d`、`bae9258`） | 未整体移植；本版采用明确的 owner EIP-712 grant + 固定预算执行器，不能声称等同 Clink 最新全量能力 |
| Clink 通用搜索兼容（`2afc83c`） | 不引入新的通用市场行为；本次固定第一方 CSV 服务，现有搜索与购买契约通过回归 |
| Hosted 原四链执行器/生产部署、预测交易场所与内部机器配置 | 原有代码保留，未作为 Monad 支付入口；内部运行材料不迁入 |
| 新增 EIP-712 预算合约、编解码、双 RPC 最终性适配与恢复 | 为本协议新增；单独测试和审查，不冒称原有 Clink 已在 Monad 验收 |

本地演练的两次 RPC 读取默认来自同一个 Anvil 实例，用于确定性闭环测试，**不代表两个独立服务商的容错性**。RPC 分歧测试另用 loopback 镜像篡改一份观察；公共模式要求两个不同主机的 HTTPS RPC。
