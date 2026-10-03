# Agentonomy Commerce Monad Implementation Plan

> **For agentic workers:** Use the available `subagent-driven-development` or `executing-plans` skill to implement this plan task by task. Use checkbox steps to record actual completion. The main agent owns architecture, security, integration and acceptance; Luna workers own bounded implementation and test tasks on non-overlapping files.

**Goal:** 在 Monad 测试网上实现用户签名、合约强制限额且可撤销的 Agent 服务采购闭环，并提供外部开发者可复用的 MCP/API 和真实验收证据。

**Architecture:** 复用 Clink Node、Core、Marketplace、Hosted 和 Watcher。新增普通合约支付轨道，链上执行用户签署的消费约束；Core 继续作为身份、业务授权、风险、预算预占和审计的控制面。7702 不属于首版必需范围。

**Tech Stack:** 现有 Python 3.12 运行时、Solidity / Foundry、EIP-712、ERC-20 有限 allowance、MCP、HTTP 商家、SQLite 持久化；Monad 网络与工具版本在部署前按官方资料核对。

**状态：** 2026-10-03 已按用户授权进入实施。完成与待办以本计划勾选、`docs/monad/acceptance.md` 和 `.superpowers/sdd/progress.md` 为准；本地完成不代表公共部署或正式提交。

## Global Constraints

- 工作仓库：`/Users/jefffeng/Echooo/AIproject/agentonomy-commerce`。基线为 `029cd0b`；规划前工作区干净。Clink 最新能力没有自动同步到此副本。
- 保留 `clink_node` MCP 协议及内部模块名；不整体覆盖最新 Clink，不修改其开发工作区，不移入内部部署材料。
- 单网络、单测试 ERC-20、单第一方对账商家、单买家账户完成首次验收。首版仅支持明确兼容的测试 EOA；不能宣称所有钱包均可用。
- 不将私钥、助记词、API 密钥、执行签名凭据或数据库材料写入源码、浏览器输出、MCP 或提交包。
- 金额按代币最小单位处理，签名和 API 传输使用确定精度表示；禁止浮点数记账。
- 所有付款经过 Core 身份、Mandate、scope、policy/risk、预占、执行复验和审计；链上验证是额外约束，不构成绕过 Core 的入口。
- 模拟演示保留明确标识。真实链路失败不得回退为模拟成功；本地测试不得自动发出公共网络交易。
- 链上付款后交付失败，恢复原订单，不再次付款。付款状态不确定时先查原交易，不重发新订单。
- 本计划不包含主网、交易策略、多链、多 Agent 撮合、退款仲裁、自有代币、通用钱包、通用任意合约调用或 7702 改造。
- 修改现有符号前运行 GitNexus impact；提交前 detect_changes；若项目未索引或工具不可用，记录缺口并先补足分析能力，不默认为已通过。
- 正式截止时间按当前报名页面：2026-10-14 11:59，Asia/Shanghai。内部目标是 10 月 12 日冻结候选版本，10 月 13 日留给验收和材料修正；这属于排期目标，不是完成承诺。
- 保存开发草稿、开发和本地验证可按授权推进；钱包签名、链上部署和首次测试付款在确切网络、地址、资产、金额、次数及 Gas 上限明确后交由用户确认/配合。正式提交保留用户最终检查。

## 已确认基础与待验证事实

- 已有：Core 授权与预算、Marketplace 购买/交付、统一 MCP、真实 HTTP 对账服务、重启持久化、订单重放测试。
- 当前参赛演示仍为模拟结算；包含执行合约源码不等于部署通过。
- 当前 `AgentonomyUSDCExecutor` 是固定 EIP-712 Execution、有限 ERC-20 allowance 的执行器，不包含本计划的完整用户链上消费授权。
- Clink 的 Polygon Amoy 验收可作设计参考，不能作为 Monad 或参赛部署的验收证据。
- 未验证：Monad 目标测试资产、目标钱包、所需 RPC 与签名基础设施、第三方风险提供方的 Monad 覆盖、公开服务实际部署状态。
- 现有仓库未授予统一 OSI 开源许可证。准备许可清单与方案可先做，最终授权需用户确定。

## 首版授权和执行设计

### 链上负责的最小边界

拟新增 `AgentonomyBudgetExecutor`，与旧 Executor 并存但按用户授权和订单固定选路。禁止同一订单在两条轨道之间自动切换。

用户签名消费授权采用 EIP-712 域隔离，域包含版本、chainId、verifyingContract。授权字段至少包括：`grantId`、`owner`、`agentScope`、`token`、固定 `payee`、`maxPerPayment`、`maxTotal`、`validAfter`、`validUntil`、`executionSigner`。金额均为 uint256 最小单位，grant/order 标识使用 bytes32。

执行许可绑定授权摘要、稳定 `purchaseId`、报价/输入承诺 `quoteHash`、精确金额和短期截止时间，由 Core 认可的执行签名路径签署。客户端不能自选 owner、Mandate、执行签名者或付款证明。用户授权与本次执行许可分别验签，不能用 Core 签名代替用户签名。

链上必须同时检查授权、域隔离、固定资产/收款方、期限、撤销状态、单笔和总额、防重放、当前执行许可。`purchaseId` 在 owner 范围内唯一，不能靠换 grantId 再次支付同一订单。合约原子更新已用额度/订单状态并转账；转账失败时全部回滚。

用户可直接通过钱包撤销自己的 grant；该路径不依赖 Core 服务在线。Core 中撤销业务权限与链上撤销分别显示完成状态，不把一个状态冒充另一个。撤销交易与付款竞态以链上实际排序判定，不承诺取消已完成付款。

新合约没有任意调用、可升级代理或可扩大用户授权的管理员接口。执行签名者的迁移不得让旧签名复活；首版若需更换签名者，采用用户重新签署新授权并撤销旧授权的明确流程。

### 继续由服务端负责的边界

- Core Mandate 与链上 grant 建立版本化、一对一且不可被 Agent 覆写的绑定；字段、网络、代币、收款方不一致时拒绝执行。
- Core 保留更细的风险/业务规则、滚动限额和并发预占；首版链上仅承诺已实现的单笔/总额等硬限制，不宣传所有风险规则都已上链。
- 广播前持久化稳定订单、签名执行许可、交易尝试标识；崩溃后先恢复已知尝试。
- Watcher 独立读取 receipt、正确合约事件、资产转移、授权/订单关联与网络最终性。不能仅凭 tx hash 或商家声明结算。
- 原始 CSV、提示词和报告不放链上；链上最小标识/承诺仍可能公开关联交易，不能宣称完全隐私。
- 交付回执和内容哈希证明关联与一致性，不证明报告准确性或强制商家交付。

## 阶段 0 冻结来源与开发基线

**可以自主完成。**

**文件：** 新建 `docs/monad/source-baseline.md`、`docs/monad/acceptance.md`；读取 `docs/source-manifest.json`、`docs/architecture.md`、`docs/verification.md`、`Makefile`。

- [x] 核对两仓工作区与已取得的远端引用，记录准确 commit；“远端最新”必须在实际核对后才使用该表述。
- [x] 创建隔离的 `codex/monad-budget-commerce` 开发分支/工作树，避免干扰原参赛演示和 Clink。
- [x] 比较与本目标有关的授权、资金、执行和恢复改动，逐项决定是否移植；不直接合并整个 Clink。
- [x] 在独立环境运行现有 Commerce、Review、合约测试，记录失败、跳过与环境限制，形成基线。
- [x] 将既有代码、比赛期新增代码和内部排除材料分开记载。

**验收：** 后续实现有固定来源、干净工作区和可复现基线；没有复制内部运行数据。

## 阶段 1 冻结协议与威胁模型

**可以自主完成；主 Agent 审查后再进入合约实现。**

**文件：** 新建 `docs/monad/design.md`、`docs/monad/threat-model.md`、`docs/monad/grant.schema.json`、`tests/monad/fixtures/grant-vectors.json`。

- [x] 固定前述 grant/执行许可的完整字段、EIP-712 类型、签名域、错误码和事件格式。
- [x] 定义订单生命周期：已报价、已预占、已广播待核实、付款已验证、已交付、已付款未交付、已证明未付款而终止；记录每个状态允许的恢复动作。
- [x] 明确 Core Mandate 与链上 grant 的绑定、总额度统计、跨授权订单防重放与撤销竞态。
- [x] 建立 Python/Solidity 共用固定签名向量；覆盖正确摘要与改金额、改链、改合约、改收款方、改订单后的失败向量。
- [x] 定义 Monad 风险覆盖：第三方提供方未支持时拒绝常规付款；若采用仅测试网、固定第一方商家策略，单独标注范围和规则，禁止伪造第三方风险结果或悄悄放宽生产策略。

**验收：** 后续合约、Core 和 Watcher 使用同一份授权/事件协议；不存在只有 Core 签名就能扩大用户授权的设计。

## 阶段 2 实现有限消费授权合约

**可以自主完成本地开发和测试，尚不广播。**

**文件：** 新建 `contracts/src/AgentonomyBudgetExecutor.sol`、`contracts/test/AgentonomyBudgetExecutor.t.sol`、`contracts/test/AgentonomyBudgetExecutorInvariant.t.sol`、`contracts/script/DeployAgentonomyBudgetExecutor.s.sol`；扩展 `contracts/README.md`。

- [x] 先写正常执行及攻击测试并确认失败，再实现用户授权、执行许可、预算、撤销、重放保护和原子付款。
- [x] 覆盖非 owner 撤销、伪签名、错误 chainId/合约、换资产/收款方、过期/未生效、单笔超额、累计超额、重复订单、换 grant 重放。
- [x] 覆盖恶意 ERC-20、转账失败、返回值异常、重入、余额不足、allowance 不足及数值边界；失败不得留下已用额度或已付款订单。
- [x] 加入不变量：成功累计付款不超过用户总额；一次订单最多付款一次；撤销生效后无法新执行；拒绝的付款不改变资金状态。
- [x] 部署脚本默认 dry-run，验证 chainId、资产代码、地址和字节码；测试代币明确命名，未经发行方核验不得称为官方 USDC。
- [x] 完成独立代码审查并修复发现，再提交本阶段。

**验收：** 单元、fuzz/invariant、格式检查通过；合约拒绝越权不能依赖前端或模型主动拒绝。

## 阶段 3 接入 Core 与执行持久化

**可以自主完成开发、迁移脚本与本地集成验证。**

**文件范围：** `apps/core/services/account_service/`、`apps/core/services/authorization_service/service.py`、`apps/core/services/funding_service/`、`apps/core/shared/hosted_facilitator_protocol.py`、`apps/facilitator/execution_models.py`、`apps/facilitator/execution_repository.py`、`apps/facilitator/authority.py`、`apps/facilitator/production.py`、`apps/facilitator/relayer.py`；新建 `apps/core/tests/test_onchain_spend_grant.py`、`apps/core/tests/test_budget_executor_recovery.py`、`apps/facilitator/tests/test_budget_executor_protocol.py`。

- [x] GitNexus 分析后仅修改必要入口；用已有授权模型的显式版本化扩展承载链上 grant，不复制一个绕过 Core 的账本。
- [x] 先测试身份/scope/授权字段绑定错误，再实现 Core grant 绑定与受控轨道选择。
- [x] 保留多窗口预算预占、现有 policy/risk 与统一审计；并发购买不能绕过预占或链上总额。
- [x] 广播前持久化 purchaseId、签名载荷摘要和交易尝试；重启、超时和多 worker 竞争只恢复同一尝试。
- [x] pending、reverted、已付款未交付分别处理；未知状态不能释放预算并创建新付款。
- [x] 数据库迁移可回退，旧订单仍按原合约/原协议恢复，不跨轨道补发。

**验收：** 现有 Core/Hosted 关键回归通过；进程中断和重试不产生第二笔经济付款。

## 阶段 4 Monad 适配与独立复验

**可以自主完成网络配置模板、RPC 只读核查和本地测试；真实地址在部署时填入。**

**文件：** `apps/facilitator/watcher.py`、`apps/facilitator/watcher_worker.py`、相关 Core 网络配置；新建 `tests/monad/test_watcher.py`、`tests/monad/test_network_config.py`、`scripts/monad/preflight.py`、`scripts/monad/verify_evidence.py`、`docs/monad/deployment.md`。

- [ ] 从官方资料和目标 RPC 核对网络、测试资产地址/精度、Gas、工具版本和实际可用的最终性判定方法；不猜资产地址。
- [x] 配置需显式列出网络、代币、执行合约与 RPC；配置不全时 fail closed，不自动接旧链。
- [x] Watcher 验证交易状态、部署合约、owner/payee/token/amount、grant/order、Transfer 事件及最终性；双 RPC 不一致时不结算。
- [x] 按 Monad 异步执行语义确定结算边界；对本项目保守验证是否需要 Verified 阶段，不直接照搬 Polygon 确认数。
- [x] 对伪造回执、其他链交易、其他合约事件、错金额/收款人、RPC 分歧、重复事件及重启恢复写失败测试。
- [x] dry-run 输出部署清单、字节码摘要、费用估算和缺项；不得在预检里自动签名/广播。

**验收：** 只有独立复验通过的实际目标链交易才能结算；本地伪造数据不能冒充公共测试网证据。

## 阶段 5 可演示产品与开发者接入

**可以自主完成代码、交互和文档；钱包实际签名在链上验收时配合。**

**文件范围：** `agentonomy_commerce/api.py`、`agentonomy_commerce/storage.py`、`agentonomy_commerce/merchant.py`、`apps/node/`、`apps/marketplace/services/purchase_service.py`；新建 `examples/monad_commerce/`、`tests/monad/test_purchase_loop.py`、`tests/monad/test_public_response.py`、`docs/monad/quickstart.md`、`docs/monad/merchant-integration.md`。

- [x] 新真实网络 composition 与 `examples/commerce/` 模拟组合隔离，不把模拟 fixture 接进真实支付服务。
- [ ] 展示连接测试钱包、授权条款、预算余额、购买结果、交易链接和撤销状态；不要求访客粘贴后台 token。
- [x] 复用真实 HTTP CSV 对账商家和持久化交付，冻结输入/报价；不新增第二个市场业务。
- [x] MCP 使用现有 preview/execute/query/result 语义，公开响应隐藏内部控制凭据与可伪造身份字段。
- [x] 给出一个新开发者可按 README 运行的最小客户端；记录安装步骤、授权步骤与失败返回，不宣称未经测量的接入时间。
- [x] 演示入口区分模拟、公共测试网和历史结果；测试网真实链上执行不等于主网真钱上线。

**验收：** Agent→MCP→Core→合约→Watcher→交付在本地链集成测试完整运行，浏览器状态能解释失败并恢复原订单。

## 阶段 6 全回归与测试网预演

**本地部分可自主完成；公共测试网部分需要部署和签名配合。**

**文件：** 新建 `tests/monad/test_end_to_end.py`、`scripts/monad/rehearsal.py`，为新增测试增加 `Makefile` 的 `test-monad` 入口；更新 `docs/monad/acceptance.md`。

- [x] 默认只在本地 Anvil/隔离服务运行，不从 CI 发送公共链交易。
- [x] 跑完成功购买、超额、过期、撤销、错误收款方、同订单重放、付款后交付失败、广播后进程退出、RPC 不一致九类验收。
- [x] 在服务端检查之外，直接向本地合约提交无效请求，验证链上自身拒绝；公共测试网按获准交易数量选择代表性用例。
- [x] 运行完整相关回归并记录新鲜结果，不能引用 9 月 22 日的通过数量代替本次测试。
- [x] 独立审查资金边界、重复执行、迁移和对外声明；安全阻断项未解决不得部署候选版本。

现有真实命令：

```sh
make PYTHON=.venv/bin/python test-commerce test-review test-submission
make PYTHON=.venv/bin/python test-workspace test-node test-e2e test-apps test-hosted
make test-contracts
```

新增后使用的命令：

```sh
make PYTHON=.venv/bin/python test-monad
```

`test-monad` 必须创建并写入 Makefile 后才可运行；它不得默认连接公共链。Go 安装器未变时不重复其测试，若发生相关修改补跑 `make test-go`。所有跳过项/环境失败逐项说明。

**验收：** 测试/审查均有可复现结果；开发完成和公共测试网验收分开勾选。

## 阶段 7 材料与外部接入

**可与开发并行准备；外部协作、许可证决定和公开发布留给后续配合。**

**文件：** 新建 `submission/monad/README.md`、`submission/monad/technical-demo-script.md`、`submission/monad/pitch-script.md`、`submission/monad/market-and-adoption.md`、`submission/monad/rights-review.md`；保留 X-Agent 既有提交包与记录。

- [ ] 核对 Logo 可用源，准备符合当前上传表单约束的文件；不复用受限品牌资产。
- [x] 完成项目定位、架构图、用户假设、获客计划、开发者 quickstart 和评委试用说明。
- [x] 梳理现有/新增功能与比赛期 commit，披露 AI 编码工具；不把导出日期当成所有代码创作日期。
- [x] 检查依赖及第一方代码权利，给用户一份具体许可证选择建议；未获确定授权前不授予统一 OSI 许可。
- [x] 准备 3 分钟技术视频和 2 分钟 pitch 的脚本；技术录屏待公共测试网验收后录制，不能拿模拟画面当真链证据。
- [x] 准备外部开发者接入包、任务和反馈表。邀请/发消息由用户授权或执行；团队内部自测不冒充外部采用。

**验收：** 每项报名陈述有对应代码、演示或实际反馈；没有虚构客户、营收、合作、审计或采用数据。

## 阶段 8 公共测试网验收与正式提交前复核

**此阶段才集中需要用户配合。**

- [ ] 展示确切部署方案：网络、测试 token、合约字节码、管理/执行地址、Gas 上限、签名来源和部署目标；取得相应确认。
- [ ] 用户在自有钱包完成测试账户授权、有限 allowance 和必要测试币准备；无需提供私钥。
- [ ] 部署候选合约并验证源码/字节码，部署独立测试服务；旧公开模拟演示保持可辨识且可恢复。
- [ ] 按明确获准的测试付款金额、次数与收款方跑通公共测试网 canary，保存 tx、receipt、日志、预算前后值、订单及交付摘要。
- [ ] 验证撤销与重放；未知链上状态先查证，不用追加付款掩盖失败。
- [ ] 完成外部接入记录及两段视频，验证评委无需内部凭据能访问材料。
- [ ] 按已确定许可证完成相应发布准备。填好实际提交材料后交给用户最终检查，检查前不最终提交。

**验收：** 公开可核查的 Monad 闭环与开发者材料齐全；页面保存成功和正式提交成功分别确认，不能混称。

## 工作分工与排期目标

| 阶段 | 建议窗口 北京时间 | 负责人 | 是否需要用户即时参与 |
|---|---|---|---|
| 0 基线、1 协议设计 | 10 月 3 至 4 日 | 主 Agent，Luna 做有界核查 | 否 |
| 2 合约、3 Core/执行 | 10 月 5 至 7 日 | 主 Agent 设计/审查，Luna 分文件执行 | 否 |
| 4 网络与复验、5 产品接入 | 10 月 7 至 9 日 | 主 Agent 集成，Luna 测试/文档 | 否，钱包兼容性缺项需记录 |
| 6 回归、8 公共测试网验收 | 10 月 9 至 11 日 | 主 Agent 验收 | 链上签名和部署时需要 |
| 7 材料、外部试接入 | 全程并行，10 月 12 日收口 | Agent 准备，用户提供必要联系与权利决定 | 后期需要 |
| 冻结、视频、最终复核 | 10 月 12 至 13 日 | 主 Agent＋用户 | 最终检查需要 |

阶段存在依赖，日期是目标。若安全或集成门槛未通过，削减可选 UI/赏金/额外功能，不削减验签、限额、复验、防重放和真实证据。不把未通过的链路描述成已上线。

## 后续需要用户配合的最小清单

1. 可用的测试钱包与必要签名：连接、消费授权、有限 allowance、撤销；私钥始终留在用户钱包。
2. 公共测试网部署/首次付款的确切范围与 Gas 预算；可复用既有独立参赛机器，但先只读核查容量和权限。
3. 第一方代码的 OSI 许可证决定及必要权利确认。
4. 如有条件，引荐一个外部 Agent 开发者试接入；没有外部接入就如实披露，不编造 traction。
5. 两分钟 pitch 中真实团队信息、出镜/配音偏好，以及最终材料检查。

除上述依赖外，基线、协议设计、合约实现、适配代码、本地验证、部署预检脚本、文档和视频脚本均可先自主完成。

## 完成定义

- [x] 本地功能完成：有限授权、链上执行、复验、交付、恢复；公共钱包装配另行验收。
- [x] 本地安全验证完成：超限/错链/错人/过期/撤销/重放/失败原子性/重启恢复。
- [ ] Monad 公共测试网验收完成：真实合约地址、交易证据和商家结果。
- [ ] 开发者接入完成：新环境可复现；外部采用证据单独标注实际情况。
- [ ] 材料完整：Logo、获客策略、源码及许可、产品链接、技术视频、pitch、访问说明、既有代码/新增工作披露。
- [ ] 用户复核完成后才进入最终提交；报名/提交不等于获奖或入选。

## 参考资料

- 当前主赛道细则：https://hackathon.monad.xyz/tracks/trust-identity-ai
- 当前提交页面：https://hackathon.monad.xyz/project?tab=submission
- Monad 部署与异步执行说明：https://docs.monad.xyz/developer-essentials/summary
- Monad 测试网信息：https://docs.monad.xyz/developer-essentials/testnet
- 本仓库 `README.md`、`docs/architecture.md`、`docs/verification.md`、`contracts/README.md`。

赛道页面当前评分为技术 20%、开发者体验 20%、原创性 15%、市场准备 25%、采用与后续计划 20%；与此前通用规则摘要不一致时，在最终提交前再核对主办方最新说明。计划以完整功能、独立证据和开发者可接入为目标，不承诺获奖概率。

## 本轮实施补记（2026-10-03）

- 本地真实 EVM、HTTP、MCP、浏览器与完整相关回归已通过，精确结果见 `docs/monad/acceptance.md`。
- 实际新增代码采用独立 Budget 子类及 `agentonomy_commerce/budget_*.py`，避免扩大原生产轨道；协议测试位于 `tests/monad/`。阶段文件范围是规划参考，不表示必须修改所有列出的模块。
- 阶段 4 官方网络与最终性资料已核查；目标公共资产/合约地址、签名设施和部署工具兼容性需在公开部署前完成。费用估算需配置完整交易与签名路径，缺项预检不会给出虚构费用。
- 阶段 5 已实现本地完整交互；真实钱包连接及外部签名器接线仍待选定钱包/设施后完成。本地临时账户组合明确拒绝公共网络。
- Logo 已找到现有 512×512 源文件并记录摘要；提交表单实际约束与最终许可仍需提交前复核。
- 阶段 8 尚未开始，未公开部署、推送或正式提交。
