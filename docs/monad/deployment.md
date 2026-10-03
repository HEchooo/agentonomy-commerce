# Monad 预算支付轨道：部署与验收

状态：本地 EVM/HTTP/MCP/UI 验收已完成；公共部署前草稿。当前没有已确认的 Monad 测试网代币、执行合约、收款方或交易地址，真实 owner wallet、execution signer 和 relayer/gas signer 也待用户提供或确认。文档中的广播步骤只能在用户明确确认网络、资产、地址、Gas 和签名范围后执行。

## 网络配置

| 模式 | chain ID | RPC 约束 | 最终性 |
| --- | ---: | --- | --- |
| `local_anvil` | `31337` | 仅 loopback HTTP；测试进程生成临时账户和本地 `TestUSD` | 本地 `local`，不代表公共链证据 |
| `monad_testnet` | `10143` (`0x279f`) | 两个独立主机的 HTTPS RPC；当前已知入口为 `https://testnet-rpc.monad.xyz` 和 `https://rpc-testnet.monadinfra.com` | 必须显式达到 Monad `Verified`；当前适配按 `finalized` 高度减 3 个区块计算 |

该 `finalized - 3` 规则来自 [Monad block states](https://docs.monad.xyz/monad-arch/consensus/block-states)，并在代码中标记为需随网络升级重新核对的版本化事实。不能把普通确认数、单个 `finalized` 标签或 Polygon 的确认规则替代它。Watcher 对本地和公共模式都要求每份观察提供与 receipt 一致的 canonical block，以及独立读取的 `finality.canonical_block`；两条 RPC 必须对 chain ID、交易、receipt、事件和同一个最终性边界块给出一致证据。

Monad 测试网资料入口是 [Monad testnet](https://docs.monad.xyz/developer-essentials/testnet)。2026-10-03 的只读核查能读取 `0x279f` 和 `finalized`，没有广播交易。目标测试代币地址目前未知，不能从旧链地址、合约 fixture 或“USDC”名称推断。

## 先决条件

- Python 3.12、项目锁定依赖和一个独立虚拟环境；Foundry 的 `forge` 和本地 `anvil` 只用于本地验证。
- 已明确的网络、chain ID、token 地址、token 代码哈希、六位精度、执行合约地址、固定 payee 地址和两个独立 RPC。
- owner 钱包可以签署 grant 并设置仅针对执行合约的有限 ERC-20 allowance；execution signer 可以签署一笔订单许可；relayer 只持有 gas 能力。公共模式的这三个真实 signer 由用户提供或确认，当前仍未完成接线。
- 所有签名、私钥、助记词和 RPC 凭据由钱包或受控签名系统管理，不写入仓库、manifest、日志、MCP 响应或截图。部署脚本本身不读取私钥。
- 经过用户确认的预算上限、付款次数、Gas 上限、测试资产范围和收款方。

公共模式的配置模型要求显式 `monad_testnet`、`eip155:10143`、`token_decimals=6` 和两个独立的 HTTPS URL；缺项时应 fail closed。当前没有地址，所以公共模式只能停在预检。

## 本地 Anvil rehearsal

本地组合会在进程内生成临时 owner、execution signer、relayer 和 payee，并部署仓库中的 `AgentonomyTestUSD` 与 `AgentonomyBudgetExecutor`。这些密钥只存在于测试进程，不能用于任何公共链。可复现命令如下：

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

当前本地 EVM composition 的 Core/Marketplace/watcher、真实合约、loopback HTTP delivery/replay、统一 MCP 和浏览器 UI 路径已通过。全回归的命令和最终结果见根拥有的 [`acceptance.md`](acceptance.md)；不要在本页复制旧的临时计数或环境失败。

本地测试的 `TestUSD` 是没有现实价值的本地测试资产，composition 明确标记 `real_funds=false` 且 `settlement_mode=local_anvil`。本地成功不能证明 Monad 部署、官方 USDC、主网资金或外部采用。

## 公共只读预检

先对两个 RPC 分别读取 chain ID 和最终化块，不发送交易。示例请求中的 URL 必须由部署方从已确认配置注入：

```sh
RPC_A='https://testnet-rpc.monad.xyz'
RPC_B='https://rpc-testnet.monadinfra.com'

curl --fail-with-body --silent --show-error "$RPC_A" \
  -H 'content-type: application/json' \
  --data '{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}'
curl --fail-with-body --silent --show-error "$RPC_B" \
  -H 'content-type: application/json' \
  --data '{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}'

curl --fail-with-body --silent --show-error "$RPC_A" \
  -H 'content-type: application/json' \
  --data '{"jsonrpc":"2.0","id":2,"method":"eth_getBlockByNumber","params":["finalized",false]}'
curl --fail-with-body --silent --show-error "$RPC_B" \
  -H 'content-type: application/json' \
  --data '{"jsonrpc":"2.0","id":2,"method":"eth_getBlockByNumber","params":["finalized",false]}'
```

预检必须同时核对：

1. 两个返回值都是 `0x279f`，并且 URL 使用 HTTPS、没有 URL 内嵌凭据。
2. `eth_getCode` 在 token 和执行合约地址返回非空代码；token 的 `symbol()`、`decimals()` 和代码哈希与获准清单一致。
3. 目标合约的 immutable `TOKEN` 和 `EXECUTION_CHAIN_ID` 与清单一致，payee 不与 owner 或 token 混淆。
4. 预检只读，不调用 `eth_sendTransaction`、`personal_sign`、Anvil 方法或任何自动签名流程。

## 合约部署脚本

`contracts/script/DeployAgentonomyBudgetExecutor.s.sol` 默认只构造合约，不广播。它要求 token 地址、目标 chain ID、token code hash 和**强制的 executor init-code hash**，并额外检查 token 有代码、`decimals() == 6`、`symbol() == TestUSD`。init-code hash 是 `keccak256(creationCode || abi.encode(token))`，包含构造参数，并在广播前与脚本计算值逐字节比较。这些限制是当前首版的测试资产策略，不是对公共 Monad 资产的认证。

```sh
export CLINK_BUDGET_TOKEN='0x...'
export CLINK_BUDGET_CHAIN_ID='10143'
export CLINK_BUDGET_EXPECTED_TOKEN_CODEHASH='0x...'
export CLINK_BUDGET_EXPECTED_INIT_CODEHASH='0x...'

forge script --root contracts script/DeployAgentonomyBudgetExecutor.s.sol:DeployAgentonomyBudgetExecutor \
  --rpc-url "$RPC_A"
```

只有在用户已经确认上述地址、Gas 预算和广播范围后，才可以把 `CLINK_BUDGET_BROADCAST=true` 交给受控 Foundry 签名配置，并使用 `--broadcast`。脚本不应被改成从源码、默认值或旧网络地址猜测 token；缺少 code hash、链 ID 不匹配或 token 不是预期 `TestUSD` 时必须失败。

部署后保存执行合约地址、token 地址、token code hash 和 init-code hash。不要把私钥、mnemonic、Foundry keystore 内容或完整签名交易中的敏感凭据写入公开材料。交易哈希、receipt 和合约地址只有在实际广播并独立核验后才能写入提交包。

## Core、Watcher 与商家配置

部署地址确认后，Core 的预算 profile 才能填写：

- `budget_mode=monad_testnet`、`budget_network=eip155:10143`、`budget_chain_id=10143`；
- 两个 HTTPS RPC；token、executor、payee 地址和六位精度；
- 允许的第一方 `merchant_id`、resource 和 payee；
- owner grant 的 `maxPerPayment`、`maxTotal`、有效期和 execution signer；
- relayer gas 上限和交易尝试持久化位置。

Core 先检查身份、既有 Spending Grant、scope、policy/risk 和预算预占，再让 backend 生成完整 calldata。Watcher 仅在两份独立证据各自带有 canonical receipt block、显式最终性 canonical boundary，并共同满足对应模式（本地 `local`、公共 `monad_verified`）后把 reservation 变成付款已验证。商家只接收已签名、已结算的 Core 收据；它不能用 HTTP 200 或自己的状态替代链上复验。

## Canary 验收与证据

公共测试网 canary 只能在明确批准的 token、payee、金额和次数内执行。至少保存以下脱敏证据：

- chain ID、RPC 检查时间、token/executor/payee 地址和代码哈希；
- owner grant 的字段摘要、grant hash、execution hash 和 allowance 范围；
- tx hash、nonce、receipt、与 receipt 一致的 canonical block、两条 RPC 的观察摘要和同一个 Verified 边界块；
- `PaymentExecuted` 与 token `Transfer` 的 grant/order/quote/owner/payee/token/amount 字段；
- Core 预算使用前后值、订单状态和商家交付输入/输出摘要；
- 撤销、重复订单、超额或错误收款方的拒绝证据（不得用新订单掩盖未知状态）。

不要上传 owner/execution/relayer 私钥、原始 CSV、完整 receipt 中不必要的个人数据、RPC 凭据或执行服务密钥。没有真实交易和商家证据时，提交材料应明确写“公共验收待完成”。

## 未知状态、失败和停止

- 广播返回未知、receipt 缺失、RPC 分歧或 Verified 边界未覆盖：保留原 reservation，查询同一 tx，禁止新建付款。
- 已验证付款但 HTTP 交付失败：进入 `paid_delivery_pending`，只重试相同 purchase 的交付；不再次扣款。
- 已证明最终化 revert：由 Core 按既有恢复路径处理并释放 reservation；普通超时不能直接判定未付款。
- 资产、payee、chain 或字节码哈希漂移：停止广播，保留证据并人工检查。
- 如果公共资产地址最终不是六位 `TestUSD` 或没有可信代码哈希，首版部署脚本和 Core profile 应继续拒绝执行，而不是悄悄切换到旧 USDC executor 或把本地 `local_anvil` 结果当成公共结算。
