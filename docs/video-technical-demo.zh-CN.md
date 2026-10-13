# Agentonomy Commerce technical demo recording manual

**Length:** no more than three minutes. **Language:** brief, natural beginner
English. **Recording:** the user controls QuickTime manually; do not automate
recording or edit an unverified result into the footage.

This recording shows one wallet-bound commerce run: view the bound wallet page,
discover the first-party CSV service in a natural conversation, send an
explicitly fictional CSV for a quote, confirm with **“Sounds good. Go ahead.”**,
make one real testnet purchase, reopen the delivered report without another
charge, then ask one natural service-quality question so the consumer Agent can
record a bounded off-chain assessment and read the shared service history.

TestUSD has no monetary value. The current deployment target is **0.001
TestUSD** (`service_amount_atomic=1000`), but the recording must read the amount
from the live `create_clink_purchase_preview` response. Do not claim that a new
public 0.001 preflight or acceptance has passed until a real run supplies that
record. The dated 0.30 TestUSD acceptance is historical evidence and is not a
replacement for the current quote.

## Off-camera setup and operator checks

Read this before recording, or provide it as the setup instruction to the
English-speaking Agent:

```text
Use only the agentonomy_commerce MCP server and the schemas returned by its live
tools. I authorize transmitting the explicitly fictional CSV I provide to the
first-party CSV reconciliation service. Allow at most one new purchase, and
only after I confirm the returned quote. If that original payment is pending,
you may reconcile it and retry delivery for the same purchase, but do not create
another purchase or payment. Wait for my next message between each numbered
prompt below. Do not record an assessment immediately after delivery. Only
after I ask, “Was the service any good? Would you use it again?”, automatically
record one bounded off-chain consumer-Agent assessment from the original task,
returned report, and observed interaction, then read service history. Do not
publish the CSV or report.
Speak in brief, natural beginner English. Do not replace the merchant's report
with arithmetic in the conversation. Do not ask the user for a numeric rating or
wallet signature. Before confirmation, stop if service identity or quote payment
terms are missing or inconsistent. After execution, stop if payment, delivery,
or output proof is missing or inconsistent, and show the actual state.
```

### Technical setup before the take

- In Codex, use only the `agentonomy_commerce` MCP server. Do not call
  `clink_node`, Core, the merchant, or wallet endpoints directly.
- Call `get_clink_connection_status` with `{}`. If the status is not active,
  call `connect_clink_wallet` with `{}` and complete the official wallet-bound
  page yourself. Show the actual status, network, wallet binding, readiness,
  and budget fields returned by the run. Do not narrate a fixed or historical
  budget.
- Call `list_clink_business_tools` with `{}` and use its returned definitions.
  Business calls go through the actual wrapper shape:

  ```json
  {
    "name": "tool_name_from_live_schema",
    "arguments": {}
  }
  ```

- Use a first-party service `offering_id` returned by live search. Generate one
  fresh run label for the preview's idempotency key. The preview must contain
  the exact synthetic CSV below. Read the quote returned by the service; the
  0.001 target is a check, not a prerecorded fact. Before purchase confirmation,
  check that the quote identifies the expected first-party service and that its
  payment terms agree across price, atomic amount, network, token, and payee.
  Settlement proof is produced by execution, so it must not be required before
  the user confirms the quote.
- If identity or quote terms are missing or contradictory, stop before purchase.
  After execution, if payment or delivery proof is missing, contradictory,
  pending without an original ID, or otherwise unknown, stop and reconcile only
  that original payment and retry its delivery; do not open another purchase or
  payment. For the four CSV rows below, the returned report should be checked
  against income **255.00 USD**, expense **40.50 USD**, and net **214.50 USD**;
  these checks never replace the returned `service_result`.

### Stop rules

- Show only values returned by the current run: wallet status, readiness,
  offering ID, quote, preview ID, purchase ID, payment/receipt fields, delivery
  state, output hash, assessment, and history. Do not reuse the historical 0.30
  IDs, hashes, budget values, or screenshots.
- The payment and delivery must be independently verified before the final
  service-quality question triggers assessment. The assessment is bounded and
  off-chain; it does not publish the CSV or report, sign a wallet transaction,
  or change the budget. Do not request a human rating as part of the default
  flow.
- A returned arithmetic check never substitutes for the merchant's
  `service_result`. If the report is absent, inconsistent with the supplied
  rows, or missing its output proof, stop with the actual response.
- Keep revocation last and optional. If it is performed, capture every earlier
  result first and show the actual wallet/MCP status afterward. Never claim a
  new public price, payment, or revocation acceptance without a fresh returned
  record.

## Short user prompts

1. ```text
   Can you open my wallet page? I think I’ve already connected it.
   ```

2. ```text
   I’ve got some income and expense records I’d like to sort out. What services
   can help with that?
   ```

3. ```text
   Here’s a small sample. I just want to know what came in, what I spent, and
   what’s left. How much would that cost?

   transaction_id,date,description,amount,currency,category
   demo-income-1,2026-10-13,Sample project,200.00,USD,services
   demo-income-2,2026-10-13,Sample sale,55.00,USD,sales
   demo-expense-1,2026-10-13,Sample hosting,-25.00,USD,hosting
   demo-expense-2,2026-10-13,Sample supplies,-15.50,USD,office
   ```

4. ```text
   Sounds good. Go ahead.
   ```

5. ```text
   Can you pull up that report again?
   ```

6. ```text
   Was the service any good? Would you use it again?
   ```

## Three-minute shot list

| Time | Screen action | Short narration |
| --- | --- | --- |
| 0:00–0:25 | Open the wallet-bound page and show the live connection state. | “The wallet sets the boundary. The Agent can see the live status, but it never receives a private key.” |
| 0:25–0:50 | Ask what services can help with the records and show the first-party CSV service. | “I am finding a useful service through a natural conversation before asking it to buy anything.” |
| 0:50–1:20 | Send the four-row fictional sample and show the returned quote. | “The sample is ready, and the service tells me the current cost.” |
| 1:20–1:48 | After the user says “Sounds good. Go ahead.”, make one purchase and show the payment, delivery, report, and output hash. | “The Agent makes one authorized testnet purchase and receives the service result.” |
| 1:48–2:08 | Pull up the same report again without another charge; reconcile only the original purchase if delivery is pending. | “Reading the report again does not charge again.” |
| 2:08–2:38 | After the user asks the service-quality question, record the bounded Agent assessment and show service history. | “The Agent now records an off-chain assessment from the task, result, and interaction. It needs no human score or signature.” |
| 2:38–3:00 | Optional final wallet revocation, only after all purchase, recovery, assessment, and history footage is saved; show the actual resulting status if performed. | “If I revoke now, that is the last action. It is optional for this take and never a reason to create another purchase.” |
