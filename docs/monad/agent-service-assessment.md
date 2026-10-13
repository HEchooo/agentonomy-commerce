# Consumer Agent service assessment

The consumer Agent evaluates a delivered service from the original task, the
returned result, and the interaction it actually observed. A user does not need
to assign a score or sign a wallet transaction for this assessment. The
assessment is an off-chain opinion associated with verified commerce evidence;
it is not a buyer's on-chain review or an independent quality certification.

## Three separate records

1. **Payment and delivery evidence:** Core's independently verified payment
   receipt and the delivered output hash. A hash establishes integrity, not
   whether a report is useful or correct.
2. **Agent assessment:** an authenticated consumer Agent's interpretation of
   task fulfillment, result quality, and the observed interaction. Its source,
   confidence, rubric, and supporting checks remain visible.
3. **Optional buyer feedback:** an explicitly supplied human rating, published
   through the separate wallet-confirmed ERC-8004 feedback flow. Neither a
   spending grant nor an Agent assessment authorizes this transaction.

The three records must not be merged into a claim that a subjective score was
verified on chain. Empty assessment or feedback history has a null average;
it does not receive an invented default rating.

## Default Agent workflow

After a purchase is delivered, the consumer Agent checks the result against the
original request. For CSV reconciliation this can include independently
recomputing totals, checking the report structure, and comparing the returned
output hash. The Agent also considers observed retries and whether recovery
required another charge. A service returning HTTP 200 is insufficient evidence
of quality, and a successful payment does not imply task completion.

When there is enough evidence, the Agent calls
`record_clink_purchase_assessment` with the original purchase ID and a bounded
assessment. It explains the conclusion naturally in the conversation without
asking the user for a numeric score. If the available task, result, or
interaction evidence is insufficient, it abstains rather than guessing.
User-initiated corrections remain feedback in the conversation; this first
version does not silently rewrite an already recorded assessment.

The hosted service does not contain a background language model and does not
invent an assessment when an order completes. The consumer Agent performs the
reasoning and explicitly records it through MCP. Agent clients that do not
perform that step still receive the ordinary payment and delivery history.

## Versioned rubric

`agent-service-v1` accepts integer dimension scores from 0 to 100:

| Dimension | Weight | Question |
| --- | --- | --- |
| Task fulfillment | 40% | Did the delivered result satisfy the requested task? |
| Result quality | 40% | What checks support the result's correctness and usability? |
| Interaction quality | 20% | Was the actual purchase and recovery process reliable? |

The overall score is the weighted total rounded to the nearest integer, with
ties rounded upward. These are an explicit pilot rubric, not a validated
industry quality standard. Confidence is `low`, `medium`, or `high`; it does
not alter the arithmetic or turn a judgment into a verified fact.

The supporting check codes are limited to `requirements_checked`,
`arithmetic_checked`, `format_checked`, `output_hash_checked`,
`recovery_observed`, `single_charge_observed`, `interaction_reviewed`, and
`limitations_noted`. They are assertions by the consumer Agent about what it
checked. It must only include checks it actually performed. Free-form report
text, conversation transcripts, CSV contents, and user secrets are not accepted
by the assessment endpoint. The conversation can contain a more useful private
explanation without copying that explanation into shared history.

## Admission and replay boundaries

Recording or reading an assessment requires a current Core-authenticated
device and that device's original delivered purchase. The service rechecks the
payment evidence and fixed service identity before accepting a record. A
pending payment, unverified receipt, different wallet, or different device
cannot provide an eligible purchase.

The record is immutable for the purchase. Repeating the same assessment returns
the same record; changing its score or check codes is rejected. A payment can
contribute only one assessment to that service's history. Replays and process
restarts do not increase sample counts, spend more budget, or send transactions.

Shared history exposes the bounded scores, source, confidence, rubric,
timestamps, and public payment/output evidence hashes. Private routing IDs,
wallet budgets, device identities, CSV, reports, and credentials are excluded.
Public hashes can still be correlated with public chain activity.

The history labels these records `authenticated_consumer_agent`,
`on_chain: false`, and `judgment_independently_verified: false`. Verified
evidence applies only to the associated payment and delivery record. A paid
purchase requirement limits repeated reviews, but does not solve collusion,
model bias, or manipulation. The current first-party pilot does not establish
a mature independent reputation market.

## Recording scope

The technical demonstration should show a fresh purchase, the same-order
replay, the consumer Agent's assessment, and its distinct entry in service
history. It should not request a human score or open the optional buyer-feedback
signing page by default. Authorization revocation remains the final action of
the formal recording, after all evidence has been saved.
