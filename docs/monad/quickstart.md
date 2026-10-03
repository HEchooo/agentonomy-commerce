# 本地真实 EVM 闭环

从本仓库根目录运行。只依赖此仓库与安装的工具，不引用同级 Clink。需要 Python 3.12、Foundry `forge` / `anvil`，以及允许 loopback 端口的环境。完整测试另需 Node.js（本次使用 23.11.0）运行浏览器会话行为检查，无需安装额外 npm 包。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps -e apps/node
forge build --root contracts
make PYTHON=.venv/bin/python test-monad
PYTHONPATH=.:apps/node .venv/bin/python -m scripts.monad.rehearsal --output .artifacts/monad-local-rehearsal.json
```

脚本启动独立 Anvil，生成内存中的临时测试账户，部署 TestUSD 与预算合约，真实签署身份、Core 授权和 EIP-712 grant，有限批准 1.00 TestUSD。随后通过真实 Core/Marketplace 和 HTTP 商家购买 0.30 的 CSV 对账报告，重启应用并重放原订单，最后撤销授权。没有公共网络交易。临时账户没有实际资金，不能用于公共链。

## 浏览器

```sh
PYTHONPATH=.:apps/node .venv/bin/python -m examples.monad_commerce.api
```

打开 <http://127.0.0.1:8090>。查看报价、确认购买、查看付款证据与报告；“查询／恢复同一订单”不创建新付款。输入在报价后冻结；付款未知时不开放新订单。无需粘贴后台 token。这个入口仅绑定 loopback，使用自动生成的本地测试账户，**不是公共钱包登录页**。

刷新网页保留当前标签页的订单引用。整个演练进程退出后临时链和状态会清理；应用子进程的重启恢复由脚本及测试覆盖。要保留可展示结果，使用上面的 `--output` 导出公开证据。不要把这个本地启动器直接绑定公网。

## Agent / MCP

配置 MCP 客户端以 stdio 启动以下命令（`cwd` 设置为本仓库绝对路径）：

```sh
PYTHONPATH=.:apps/node .venv/bin/python -m examples.monad_commerce.node
```

使用 `list_tools` 读取现有统一 Node 工具定义。顺序为服务发现 → 服务详情 → `create_clink_purchase_preview` → `execute_clink_purchase` → 查询原订单/结果。只提交 `offering_id`、CSV、幂等请求标识和返回的 preview/order ID；不能传 owner、Core grant、签名器或付款证明覆盖身份。实际 stdio 客户端示例见 `tests/monad/test_mcp.py`。

## 失败时

- receipt 缺失、最终性未达到或 RPC 不一致：预算继续预占，查询原交易。
- 已付款未交付：恢复原订单交付；不要创建新付款。
- 超额、过期、撤销、错收款方或签名无效：拒绝执行；链上合约也独立检查这些约束。
- 配置错误或签名缺失：停止，不降级到模拟成功。

## 公共 Monad 测试网

`examples/monad_commerce/monad.example.json` 是未填写的公开部署模板。预检不会签名或广播：

```sh
.venv/bin/python -m scripts.monad.preflight examples/monad_commerce/monad.example.json
```

缺少真实 token、executor、payee 时退出码为 2，这是预期的停止点。公共地址、Gas 范围、用户钱包签名和执行签名设施尚待确认；本地账户启动器明确拒绝公共链。请按 [部署说明](deployment.md) 完成公开验收前置条件，再装配真实钱包/受控签名器。当前工具 Foundry 1.7.1 的本地测试不代替公共 Monad 所需工具兼容性核查。
