# Market and adoption plan

This is a hypothesis and measurement plan. An owner-controlled Monad testnet
canary is now complete, but it does not report customers, revenue, partnerships,
or external developer usage; none is recorded in the current materials.

## Current canary evidence

The completed canary used one owner-authorized `0.30 TestUSD` payment on chain
`10143`. The receipt was verified through two RPC observations, the CSV report
was delivered once, and the original order was recovered and queried again
without another payment. The API and merchant ran over loopback HTTP; the
public review site remains a separate simulation. This is internal acceptance
evidence for the payment and recovery boundary, not external adoption.

## Initial user and job

The first target is a developer building an agent that must purchase a bounded, deterministic service on behalf of a user. The developer needs:

- a single agent-facing MCP/API entry instead of a custom wallet integration;
- a quote that freezes the service input, payee, asset, network, and price;
- a user-readable spending limit with revocation and expiry;
- evidence that a payment was independently confirmed before delivery;
- safe recovery when a process or merchant fails after payment.

The first merchant shape is a CSV reconciliation service because the request and result can be made deterministic, the input can be hashed, and delivery can be tested without handling private data. This is a beachhead for validating the payment/control boundary, not a claim that reconciliation is the whole market.

## Why this could matter

Agent builders currently assemble authorization, budget, payment, and service delivery themselves. The proposed value is a reusable control path: Core keeps business authority, while the budget contract gives users an on-chain hard ceiling that a retrying agent cannot silently exceed. A developer can adopt the MCP/API semantics and a merchant receipt contract without designing a new arbitrary wallet.

The main product hypothesis is:

> If an agent can request a quote, obtain a bounded user grant, pay an exact amount, and recover a failed delivery without a second charge, developers will prefer this path for paid service calls over handing the agent an unrestricted wallet.

This must be tested with conversations and runnable integrations. It is not inferred from the existence of the code or a local test passing.

## Distribution and adoption loop

1. Publish a clean source snapshot and the local quickstart after rights and deployment review.
2. Give a developer one fixed offering, one grant schema, a local Anvil recipe, and a small client that uses `preview -> execute -> result`.
3. Ask external developers to attempt one purchase, one replay, and one failure recovery; record the exact environment and errors.
4. Fix documentation and protocol friction, then repeat with a second merchant-shaped service only after the first path is understandable.
5. For the completed Monad canary, provide a read-only transaction and finality
   proof; do not require a developer to receive internal review credentials.

Potential distribution channels are the source repository, the `clink_node` MCP ecosystem, agent framework examples, and direct developer trials. No outreach or external messaging has been performed by this draft.

## Measures to collect

The following are future measures, not current results:

| Question | Evidence to collect |
| --- | --- |
| Can a new developer start? | Time measured from a fresh environment to the first local preview, with setup blockers recorded |
| Is the authorization model understandable? | Correctly completed grant fields, wrong-field rejection, and a short comprehension interview |
| Is recovery trusted? | Successful replay after restart; no second payment after delivery failure; developer explanation of the state |
| Does the merchant contract generalize? | Number and type of additional deterministic services that can use the same receipt and quote semantics |
| Does public-chain evidence help? | Successful read-only verification of a canary transaction by someone outside the team |

Do not publish a conversion rate, integration time, satisfaction score, customer count, or retention number until it has a dated source and an agreed measurement method.

## Near-term sequence

- **Canary evidence package (complete):** retain the passing local EVM
  HTTP/MCP/UI evidence alongside the owner-controlled Monad payment, its
  two-RPC proof, delivery result and same-order recovery evidence. Keep the
  local Anvil/TestUSD composition visibly separate from public-chain evidence.
- **Public product endpoint:** the user chose a reviewer-accessible own-wallet
  HTTPS product path. Website wallet login, OPC binding and account isolation
  are under design review; the actual purchase UI/API remains loopback and the
  public review site remains simulated. Do not claim the multi-wallet product
  is deployed before its acceptance passes.
- **Developer trial:** ask a small number of external developers to run the quickstart and fill the feedback form. Record invitations and results separately; internal tests are not external adoption.
- **Iteration:** prioritize failures that affect authorization clarity, idempotency, payment evidence, or recovery before adding more services or networks.

## Risks and stop conditions

Stop or narrow the plan if the testnet asset is not verifiable, two RPCs cannot provide the required evidence, the finality rule changes without an updated adapter, the merchant needs to bypass Core, or recovery could create a second payment. Do not compensate for missing public evidence with a local screenshot.

The current plan notes indicative track weights of technical 20%, developer experience 20%, originality 15%, market readiness 25%, and adoption/follow-through 20%. These weights are a planning reference only and must be checked against the official form immediately before submission.
