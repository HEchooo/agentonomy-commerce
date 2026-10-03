# External developer feedback form

This is a blank collection template. No external developer feedback or adoption is recorded in the current materials. The local Anvil/TestUSD composition now exercises the EVM, HTTP, MCP and UI paths; internal tests and team review must remain separate from external evidence.

## Participant and run record

```text
participant_id: PENDING
organization_or_project: PENDING / may be anonymous
date_utc: PENDING
consent_to_quote_or_publish: false
environment: PENDING (OS, Python, Foundry, wallet/RPC mode)
commit_or_package: PENDING
facilitator: PENDING
```

Do not collect private keys, seed phrases, wallet exports, review tokens, raw customer CSVs, or production credentials. A participant may use synthetic data and local Anvil. Public testnet runs require an explicitly approved wallet, asset, amount, and transaction range.

## Task

Give the developer the exact clean package and ask them to complete the following without an internal credential:

1. Read the protocol and run the local quickstart.
2. Find the fixed merchant resource and identify token, payee, price, and budget limits from the preview.
3. Complete one local purchase with synthetic CSV input.
4. Replay the same purchase after a process restart and confirm that the purchase ID, result, and budget usage do not change.
5. Trigger one safe failure (expired quote, wrong scope, or malformed receipt) and explain which layer rejected it.
6. If a public canary is available, verify its transaction through the documented read-only evidence path; otherwise label the run local only.

Record commands and timestamps. If a step cannot run because the environment blocks Anvil or lacks a dependency, record the exact error instead of substituting a simulated success. A successful local run must remain labelled `local_anvil`; it is not a public Monad or real-funds result.

## Questions

Use the participant's words where permission allows:

1. What did you think Core controlled, and what did you think the contract controlled?
2. Could you identify the exact token, payee, amount, per-payment limit, total limit, expiry, and purchase ID before approving?
3. Which error or state made recovery difficult to understand?
4. Did the `preview -> execute -> result` flow fit your client, or did you need an undocumented assumption?
5. Could you tell the difference between local Anvil/TestUSD, Monad testnet, and a real-funds deployment?
6. What service would you try first, and which field or protocol boundary would block integration?
7. What evidence would you require before trusting a payment result in your own agent?

## Structured result

```text
setup_completed: unknown
first_preview_completed: unknown
first_purchase_completed: unknown
replay_without_second_payment: unknown
failure_case_understood: unknown
public_transaction_verified: unknown / not_applicable
blocking_issue: PENDING
suggested_doc_or_api_change: PENDING
measured_setup_time: PENDING (do not estimate)
follow_up_allowed: false
```

Attach only redacted logs, error codes, and synthetic IDs. Keep any quote or testimonial separate until the participant gives explicit permission. A participant's successful local run is evidence of a developer experience observation, not evidence of adoption, revenue, security audit, or production readiness.

## Current status

No invitations, external messages, interviews, or feedback collection have been performed by this draft. The team contact for a future authorized trial is `fengjie@alvinsclub.ai`; no Telegram handle is supplied.
