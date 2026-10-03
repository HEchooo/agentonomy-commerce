# Monad 预算支付轨道：商家接入

本文描述当前首版的第一方 CSV 对账商家和其接入边界。它是本地可运行的开发者合同，不是已上线的公网商家 SLA。当前逻辑资源 `https://merchant.agentonomy.invalid/v1/reconcile` 使用 `.invalid` 域名，实际本地组合只把它映射到 loopback HTTP；没有公网商家地址。

## 角色和边界

商家只负责接受已授权、已结算且输入冻结的订单并返回服务结果。只有 Core 完成独立 watcher 验证后，商家才接收 receipt；本地和公共模式都要求每份观察提供 canonical receipt block 与显式最终性 canonical boundary，并让两份 RPC 观察对同一边界达成一致：

1. Core 校验 wallet identity、既有 EIP-191 Spending Grant、policy/risk、预算预占和链上付款证据。
2. Marketplace 生成固定报价和 purchase ID，向 merchant 发送 Core 签名的 payment receipt。
3. Merchant 校验 receipt 的签名、scope、订单、网络、资产、payee、价格和 `settled` 状态，再校验请求体哈希与 Core 预览中的输入哈希一致。
4. Merchant 执行 CSV 对账，将结果按 purchase ID 持久化；重复请求返回同一结果，不触发第二次付款。

商家不保存用户私钥、不创建 grant、不选择 owner 或 execution signer、不改变报价，也不把 HTTP 200 当作链上支付证明。商家返回失败时，Marketplace 按原 purchase 恢复；已付款订单只能重试交付。

## 当前 HTTP 合同

逻辑请求：

```http
POST https://merchant.agentonomy.invalid/v1/reconcile
Content-Type: application/json
Idempotency-Key: <purchase-id>
X-CLINK-PAYMENT-RECEIPT: <Core-signed-receipt>

{"csv_text":"transaction_id,date,description,amount,currency,category\n..."}
```

开发环境的 transport 只把这个固定资源映射到 `http://127.0.0.1:<port>/v1/reconcile`；Agent、MCP 输入或商家响应不能修改映射。请求 body 必须只有 `csv_text`，`Idempotency-Key` 是 purchase ID，receipt header 不能省略。Receipt scope 至少固定以下字段：

```json
{
  "merchant_id": "commerce_analytics",
  "resource": "https://merchant.agentonomy.invalid/v1/reconcile",
  "purchase_id": "<purchase-id>",
  "chain": "<Core-selected-network>",
  "token_address": "<Core-selected-token>",
  "pay_to": "<Core-selected-payee>",
  "amount_usdc": "0.30",
  "amount_atomic": "300000",
  "status": "settled"
}
```

Receipt 的签名密钥只由 Core/商家部署配置持有，不进入浏览器或公开 API。商家还会把 `{"csv_text": ...}` 以确定 JSON 形式做 SHA-256，并与 Core 保存的 preview input hash 比较。缺少 Core input hash、receipt scope 不匹配或输入被替换时，应拒绝交付。

## CSV 约束与结果

首版接受一个 UTF-8 CSV，表头必须包含且仅包含：

```text
transaction_id,date,description,amount,currency,category
```

约束如下：

- CSV 不超过 128 KiB，HTTP body 不超过 256 KiB，最多 1,000 条非空数据行。
- `date` 为 ISO 日期；`currency` 为三字母代码，单个文件只允许一种币种。
- `amount` 必须是两位小数的十进制字符串，绝对值不超过 1,000,000,000,000；服务端使用确定精度，不使用浮点数记账。
- `transaction_id`、`description` 和 `category` 不能为空。完全相同的重复行计入输入行数，但只计入一次汇总；同 ID 内容不同会拒绝。
- 返回收入、支出、净额、分类汇总、输入行数、唯一行数、重复 ID 和输入哈希。结果带有 `real_funds=false` 与 `settlement_mode=local_anvil` 的本地环境标记。

价格当前固定为 0.30 的本地计价单位，即 300000 个六位 TestUSD 最小单位；这属于本地 Anvil/TestUSD composition 的产品参数，不是官方 USDC 或主网价格承诺。

## 响应和幂等

| 情况 | 状态/错误 |
| --- | --- |
| receipt、输入和 CSV 均通过，首次交付 | `200`，保存结果 |
| 同一 purchase ID、同一输入的重试 | `200`，返回持久化结果 |
| 同一 purchase ID 换输入 | `409 idempotency_conflict` 或 `409 input_hash_mismatch` |
| receipt 缺失或 scope/签名/状态不符 | `401 invalid_payment_receipt` |
| body、CSV 或 header 不符合约束 | `400 invalid_request`（过大可为 `413`） |
| 保存结果前 Core 输入哈希不可用 | `503 input_hash_unavailable` |
| 已过结果保留期的 tombstone | `410 stored_result_expired` |

结果 payload 默认保留 7 天；过期后保留幂等 tombstone，防止相同 purchase 被悄悄计算为新结果。具体保留时间是当前本地实现，不是商家对生产数据的长期存储承诺。

## 开发者接入路径

开发者应从统一 `clink_node` MCP 或 Commerce API 的 `services -> preview -> execute -> purchase` 语义接入，不应直接构造商家 receipt 或绕过 Core：

1. 读取目录，确认 `offering_id=csv-reconciliation-v1`、资源、网络、资产、payee 和价格。
2. 提交带 `Idempotency-Key` 的 preview，等待报价冻结；不要在 preview 过期后复用新输入。
3. 执行现有 preview；Core 自己分配 grant、scope、reservation 和签名路径。
4. 如果执行返回未知状态，读取原 purchase；不要新建订单重试。付款已验证但交付失败时只重试原 purchase 的交付。
5. 只把 `service_result` 当作已完成交付；单独的 HTTP 200、商家声明或浏览器截图不能替代付款证据。

添加新的商家或资源需要同时更新 Core allowlist、第一方 testnet policy、报价和 receipt scope，并补充独立测试。第三方 risk provider 未覆盖 Monad 时应拒绝常规付款，不能伪造 provider 结果或把测试网白名单写成生产风控。

当前本地 composition 已通过真实 EVM、loopback HTTP delivery/replay、统一 MCP 和浏览器 UI 的联通路径；最终全回归及精确结果见 [`acceptance.md`](acceptance.md)。本地 `TestUSD` 结果的 `settlement_mode` 是 `local_anvil`，不应写成 simulated 或公共链结算。

## 公开接入状态

当前没有已确认的公网 endpoint、测试网交易、外部开发者反馈或采用数据。公共 composition 仍是 local-only；真实 owner wallet、execution signer、relayer/gas signer、批准的资产和部署信息待用户提供或确认。loopback HTTP 和本地 Anvil 的测试可以说明组合边界和恢复语义，不能写成“商家已上线”“开发者已接入”或“Monad 已验证”。
