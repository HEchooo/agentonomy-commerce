# Two-minute pitch script

“Agents are good at finding services, but giving an agent a spending budget still leaves a hard question: what exactly can it pay for, and how do we know a retry will not pay twice?

Agentonomy Commerce makes that boundary explicit. Core remains the control plane for wallet identity, the existing signed Spending Grant, policy and risk, budget reservations, execution, and audit. For the payment rail, the user signs an EIP-712 budget grant that fixes the token, merchant payee, agent scope, per-payment limit, total limit, validity window, and execution signer. Core signs a second permit for one frozen quote and one purchase ID. An ordinary ERC-20 executor checks both signatures, revocation, replay state, and exact transfer amounts on chain.

The result is a purchase flow that can be inspected by a user and a developer. A Marketplace preview freezes the CSV input and a 0.30 local TestUSD price. Core reserves the budget, the contract enforces the signed ceiling, and the watcher independently checks the transaction, the `PaymentExecuted` and `Transfer` events, the receipt's canonical block, and the same explicit finality boundary through two RPC observations. The merchant receives a signed settled receipt and the same frozen input. If delivery fails, the system retries delivery; it does not charge a second time. If the chain result is unknown, it keeps the original reservation and asks the operator to inspect the original transaction.

The design is deliberately narrow. It uses a normal ERC-20 contract, with no EIP-7702 requirement, generic arbitrary-call API, or claim that a test token is official USDC. It keeps business authorization in Core instead of creating a second wallet ledger. The local composition now exercises the protocol and recovery path with real Anvil EVM transactions and loopback HTTP. That local proof is clearly labelled `local_anvil`; it is not a Monad deployment.

Our first market hypothesis is agent builders who need to buy deterministic services such as reconciliation, enrichment, or analysis without handing the agent an unrestricted wallet. We will validate that hypothesis with an external developer quickstart, a small first-party merchant, and measured integration feedback. At this point we have no recorded external customers, revenue, partnerships, audit, or adoption claim.

Monad testnet chain ID 10143 has been checked through read-only RPCs. The testnet token, executor, payee, public transaction, and public service URL remain pending user-approved deployment. Agentonomy is the team; contact is fengjie@alvinsclub.ai. The ask is simple: review the bounded authorization model, run the local quickstart, and tell us whether this is the payment boundary your agent service needs.”

Keep the disclosure in the last paragraph if the recording is made before public acceptance. Do not say “live,” “audited,” “adopted,” or “official USDC” unless separate evidence has been added to the exact submitted commit.
