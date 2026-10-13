# Public Monad micro-price acceptance — 2026-10-14

This record documents the verified public micro-price preflight on
`review.agentonomy.xyz` using release
`71a9289bf7f8cc4d2adfc8f00177e4b6ee8a5c72`. It is a Monad testnet demonstration;
TestUSD has no monetary value. No production or mainnet action, private wallet
key, AWS pin, session ID, private path, secret, CSV body, or report body is
included here.

The run started around `2026-10-14 00:42 CST` with the current authorized
wallet state. It did not require a new budget signature, allowance signature,
or buyer-rating signature. A human wallet login signature is still acceptable
if a fresh login is needed for a recording; login does not renew the grant.

## Verified purchase and delivery

| Field | Evidence |
| --- | --- |
| Network | Monad testnet, chain `10143` |
| Preview / purchase | `preview_28588c34eaa6` / `purchase_28588c34eaa6` |
| Price | `1000` atomic units = `0.001 TestUSD` |
| Payment | [`0x52b462f432de970ad6d33e42f55144c38ab0d21c4bce921b30fb1f27e482e68e`](https://testnet.monadexplorer.com/tx/0x52b462f432de970ad6d33e42f55144c38ab0d21c4bce921b30fb1f27e482e68e) |
| Payment sequence | Initial `payment_submitted` → recovery of the same transaction → `delivered` |
| Receipt | Status `1`, block `70731638`, finality block `70731719`; two RPC observations agreed |
| Input hash | `0x64464ee1738337e18d65141976e9434bd8a7ac47af5facc399e22f5c7e7b3da1` |
| Output hash | `0x2c4b22dba19ab7e04ad5a9b4abdee2660974328239916fa2e8e3eb7039c1629e` |

The original payment transaction was recovered without replacement. A read of
the purchase and a repeated execute of the same preview both returned the same
delivered order, original transaction, and original payment hash.

The returned report had four unique CSV rows. Independent checks matched income
`255.00 USD`, expense `40.50 USD`, net `214.50 USD`, and the exact category
totals. The input and output hashes above are the integrity evidence; the CSV
and report contents remain private.

## Budget, nonce, and delivery counters

The budget projection changed from used `0.90` to `0.901` TestUSD. Reserved
amount remained `0`; remaining budget changed from `0.10` to `0.099` TestUSD.
The relayer nonce changed from `9` to `10`; this is a nonce transition, not a
payment count. The observed delivery count changed from `3` to `4`.

## Consumer Agent assessment and public history

After verified delivery, the authenticated consumer Agent recorded one bounded
off-chain assessment:

- task fulfillment: `100`;
- result quality: `95`;
- interaction quality: `80`;
- confidence: `medium`;
- weighted score: `94` under the 40/40/20 rubric;
- source: `authenticated_consumer_agent`;
- judgment independently verified: `false`;
- assessment hash: `sha256:6803dd421bfda789210582641011e46911767f3f03e13467f276c92fa28f046f`.

These are the consumer Agent's off-chain judgments, not human ratings and not
independently verified quality claims. No human rating or wallet signature was
used, and buyer feedback remains `0`.

A fresh isolated read-only client listed 13 business tools and successfully read
the account, catalog, report hash, assessment, and shared history. The resulting history showed
`4` verified deliveries, `2` assessments with mean `93`, and `0` buyer-feedback
samples. The read-only verification did not create a purchase, change the
budget, or submit a rating.

## Installation and recording boundary

One active Commerce bridge was used for this preflight. Issuing a new
installation token revokes older installation tokens; the temporary diagnostic
client refreshed the token and invalidated the coordinating window's cached
token, causing its later request to return `401`. This does not
demonstrate multi-client coexistence, and the recording must not claim it.

For a recording, keep one active Commerce bridge and no other MCP diagnostic
clients. Before recording, check connection, account readiness, service price,
and service identity in the DEMO window, without creating a preview or purchase.
Restart that bridge first only if its connection is unavailable. The temporary
diagnostic client must not be used as a second concurrent client.

If a later payment is pending, continue querying or recovering the original
purchase without requesting confirmation again. Stop only when evidence
conflicts or remains unknown after bounded checks; never create a replacement
purchase to resolve an uncertain original payment.

No architecture or token-service change was made for the micro-price run.

## Historical boundaries and remaining gates

The final code passed 837 Monad tests and 21 Core budget/recovery tests. The
Commerce, review, and submission suites passed 25, 85, and 6 tests respectively.
Local Anvil coverage includes exact 0.001/0.0002 payments, restart/replay without
another charge, and recovery of a historical 0.30 paid order after a price
change. These local checks are separate from the public evidence above.

The dated 0.30 TestUSD purchase remains preserved in
[the 2026-10-13 acceptance record](public-wallet-acceptance-20261013.md).
The local simulated 0.30 example remains explicitly simulated in the README.
Neither is substituted for the verified 0.001 public preflight above.

The following gates remain outside this acceptance record:

- Core and on-chain revocation followed by a refused new purchase;
- second-wallet isolation;
- optional buyer feedback with an explicit wallet action;
- technical recording, publication of the existing pitch film, final form
  review, and submission.
