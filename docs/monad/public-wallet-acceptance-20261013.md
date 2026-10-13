# Public Monad wallet acceptance — 2026-10-13

This record captures the fresh owner-wallet purchase run after the public
deployment preflight. It is testnet evidence for Agentonomy Commerce, not a
production-readiness or security-audit claim. TestUSD has no monetary value.

## Purchase and delivery

| Field | Evidence |
| --- | --- |
| Run | `public-rehearsal-20261013132037-9d10c5e` |
| Network | Monad testnet, chain `10143` |
| Preview / purchase | `preview_d374cffac2a8` / `purchase_d374cffac2a8` |
| Amount | `0.30 TestUSD` |
| Payment | [`0x97a3318a99ff9ecb4aec97aa76bc504fd63f96a44c1c5fecd7a74794347e8d00`](https://testnet.monadexplorer.com/tx/0x97a3318a99ff9ecb4aec97aa76bc504fd63f96a44c1c5fecd7a74794347e8d00) |
| Receipt block | `70692227` |
| Block hash | `0x108776c60ded4247528020a40866a45eacae6a6d06fd456d363cf873e5bb47d2` |
| RPC result | Two RPC observations agreed; finality was classified `monad_verified` and the canonical payment hash matched |
| Delivery output hash | `0x21701fdadede68174bf335856d9ffc101935f674513f1eddeb77995a6aeca8c2` |
| Report integrity | The canonical report SHA-256 was independently recomputed and matched |
| Delivery count | `1` |

The same preview was executed again after the first delivery. The response
returned the original purchase, receipt, payment transaction and output, with
no second charge. This demonstrates same-order idempotency and recovery for the
recorded purchase.

## Budget and history after the run

- Used: `0.30 TestUSD`
- Reserved: `0.00 TestUSD`
- Remaining: `0.70 TestUSD`
- Shared history: one verified delivery record
- Optional buyer feedback: not submitted in this run and not counted in shared
  history
- Consumer Agent assessment: one off-chain record, checked on the public MCP
  endpoint after deployment of `dec6e0a82578c1dba2679ce226f9ff869066bb13`

## Automatic consumer Agent assessment

The consumer Agent independently checked the four synthetic input rows against
the returned income, expense, net, category, and row-count values. These checks
and the canonical output hash matched. The Agent assessed task fulfillment at
100, result quality at 100, and interaction quality at 60: original-order
reconciliation was needed before delivery completed, while replay did not
charge again. The fixed 40/40/20 rubric produced **92**, with **medium**
confidence because this is a small synthetic example.

This is an Agent judgment, not an independently verified quality score. Its
linked payment and delivery evidence are verified separately. No human score,
wallet signature, transaction, or additional budget was required to record it.

- Recorded at: `2026-10-13T14:02:41.113467Z`
- Assessment hash:
  `sha256:0f4aa72b8f7b8c63acf1c750c75d17d3356224f94f254b6a59c818f854d8c45d`
- Exact assessment replay and readback returned the original record.
- A further original-preview replay after deployment returned the same payment,
  receipt, and output. Budget stayed at `0.30 used / 0.00 reserved / 0.70 remaining`;
  relayer nonce stayed at `7`.
- Final history observation at `2026-10-13T14:04:49.816206Z`: one verified
  delivery, one Agent assessment, and zero buyer-feedback samples.
- Public labels: `source=authenticated_consumer_agent`, `on_chain=false`,
  `judgment_independently_verified=false`.

The [assessment contract](agent-service-assessment.md) describes the rubric,
privacy, source, and replay boundaries.

## Still pending

The following acceptance actions were deliberately left for the final review
and recording pass:

- fresh Core and on-chain revocation, followed by a refused new purchase;
- second-wallet isolation;
- technical and pitch videos, final form review, and submission.

Optional buyer feedback is outside the current acceptance result. The canceled
wallet feedback request produced no submitted transaction and no verified
rating.

The public endpoint is a Monad testnet demonstration. No AWS account, signing
key, temporary session, private deployment path, or operator credential is part
of this public record.
