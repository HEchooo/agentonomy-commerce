# Monad submission copy — ready for final form review

Prepared on 2026-10-07 for **Agentonomy Commerce**, team **Agentonomy**, contact
`fengjie@alvinsclub.ai`. The authenticated form was inspected and the project description and judge
access instructions were saved on **2026-10-07 at 15:22 UTC**. The entry is a
saved draft, **not a final submission**. Its checklist is **3/5 complete**:
primary track, project details and logo are complete; live product and videos
are incomplete. The observed deadline is **2026-10-14, 11:59 GMT+8**.

[form-saved.json](form-saved.json) contains the exact currently saved fields.
The copy below is reusable supporting text; it is not a claim that every
paragraph below was separately entered into the form.

## Observed form requirements

| Required field | Observed constraint / status |
| --- | --- |
| Primary track | Trust, Identity & AI Infrastructure selected |
| Project logo | Uploaded; PNG/JPG/WEBP, max 2 MB, at least 500 px, up to 4 million pixels |
| Name / one-liner | 120 / 200 characters; saved 19 / 93 |
| Description / go-to-market | Each up to 8,000 characters; saved 2,469 / 1,485 |
| GitHub | Public, or shared with metropolis@hackathon.monad.xyz; public repository supplied |
| Live product | Required HTTPS URL; must run on Monad Mainnet or Testnet; empty |
| Technical demo | Required hosted video URL, working product, up to 3 minutes; empty |
| Pitch | Required hosted video URL, team/problem/why, up to 2 minutes; empty |
| Judge access | Optional, up to 8,000 characters; saved 1,264 |

The inspected form has no license field, and the current selected-track
deliverables do not state a uniform OSI condition. Earlier license guidance
has not been confirmed as a current event-wide requirement; check general
terms and final declarations before submission. This observation grants no
license. [official-requirements.json](official-requirements.json) records the
current form/track requirements, including the stricter form logo limit. Bounties, a 30-second promotion clip and the project
X link are optional. No placeholder URL was entered.

## Project name

Agentonomy Commerce

## One-line description

Let AI agents discover and purchase services within finite, revocable user-authorized budgets.

## Short description

Agentonomy is financial infrastructure for AI agents that act. Users authorize
an exact asset, merchant, spending limit, policy and expiry; within that finite,
revocable boundary, an Agent can discover and purchase services without a new
per-purchase wallet signature when policy permits. Core checks identity, policy
and budget, reserves budget before execution, and a Monad contract enforces the
signed limits. Payment is independently verified, delivery is recorded against
the returned output hash, and private keys stay in the user's wallet. If
delivery is interrupted, the same paid order can recover without another charge.

Verifiable payment and delivery records, together with explicitly supplied
buyer feedback, build a service history for future selection. The current pilot
has one first-party CSV reconciliation service; it demonstrates the
transaction-to-history loop, while a mature multi-merchant reputation ranking
is a future expansion.

## Problem and solution

An agent that can discover a useful API still needs a safe way to pay for it.
Handing it an unrestricted wallet gives it more authority than a service
purchase requires, while a retry after a timeout can create uncertainty about
whether the user has already paid.

Agentonomy Commerce connects discovery, a fixed quote, spending authorization,
payment verification and delivery through one MCP interface. A user-signed
budget fixes the token, payee, per-payment cap, total cap, validity and execution
signer. Core retains identity, policy, budget reservations and audit. The
contract adds an independently enforced ceiling. Each execution is bound to one
purchase and quote, and delivery recovery keeps the original payment identity.

## Historical local acceptance (2026-10-07)

The following is a dated local acceptance record saved with the form; it is not
evidence of the current public deployment or a completed fresh public purchase.

For that acceptance, TestUSD and AgentonomyBudgetExecutor were deployed on
Monad testnet, chain 10143.
An OKX owner completed wallet identity, the business mandate, an EIP-712 budget
grant and finite 1.00 TestUSD allowance. The first purchase was initiated through
the actual clink_node MCP and paid 0.30 TestUSD. Core and its watcher verified the
receipt through two RPCs. After a transport interruption, the original order
recovered and delivered a CSV reconciliation report. Repeating recovery and
query returned the same report and transaction: one payment, one delivery,
0.30 used and 0.70 remaining.

The report reconciles two synthetic records: income 10.00 USD, expense 2.00 USD,
net 8.00 USD. TestUSD has no monetary value and is not official USDC. The
completed test uses a loopback merchant/API on the operator's machine. The
public review website provides a separate, explicitly simulated demonstration.
Live Core-plus-onchain revocation is implemented and locally tested; its public
testnet owner-wallet acceptance remains to be recorded.

## Public deployment preflight (2026-10-13)

On 2026-10-13, the commerce release was deployed publicly with wallet state
retained. Anonymous account and status access returned 401; binding and feedback
assets returned HTTPS 200; a clean external MCP client listed all 11 business
tools and queried search, service details and shared history. Two RPC providers
agreed on the chain-10143 deployment and service identity, and both isolated
signer KMS DryRuns passed. The buyer's budget and finite allowance were prepared,
but fresh device consent, a new own-wallet purchase and delivery, same-order
replay, buyer feedback signing and readback, and revocation remain pending. No
purchase was executed or broadcast during this preflight.

## Monad integration

Monad executes the user's signed spending constraints and the per-purchase
execution permit. AgentonomyBudgetExecutor checks signatures, the chain and
contract domain, the fixed token and payee, expiry, revocation, spending caps and
purchase replay state before transferring the exact ERC-20 amount. The watcher
checks the receipt, transaction identity, payment and transfer events and the
same canonical finality boundary through two RPC observations.

This version uses an ordinary ERC-20 executor. It does not require EIP-7702.
The CSV input and report stay offchain; their commitments associate the payment
with the agreed request and delivered result.

## Architecture and reused work

Agent → clink_node MCP → Marketplace → Core → Monad budget executor → watcher → merchant result.

Core, Marketplace and Node are first-party source copied from Clink into this
standalone repository. They provide identity, signed mandates, policy/risk,
reservations, audit, service discovery, quotes, purchase state and MCP contracts.
The Monad work adds the EIP-712 budget rail, network/backend and watcher
integration, the isolated signer and external-wallet composition, and paid-input
recovery. There is no runtime dependency on another Clink checkout. Imported
source predates this hackathon work; the export date is not a creation date for
the entire tree.

AI coding assistance was used for implementation, testing, review and material
preparation. Imported code and the Monad additions are documented in the source
baseline and Git history. This disclosure is not a security-audit claim.

## Intended users and next steps

The first intended users are agent developers purchasing deterministic APIs,
reports or other metered services for users. The CSV merchant provides a small,
verifiable first integration. Next we will complete public revocation acceptance,
publish the recorded technical and pitch videos, and invite external developers
to try the quickstart and provide measured integration feedback. No external
customers, revenue, partnerships or adoption metrics are claimed.

## Links and evidence

| Field | Value |
| --- | --- |
| Source repository | https://github.com/HEchooo/agentonomy-commerce |
| Website | https://agentonomy.xyz/ |
| X | https://x.com/AgentonomyXYZ |
| Public wallet / MCP endpoint | https://review.agentonomy.xyz/ — deployed on Monad testnet; fresh own-wallet purchase acceptance pending |
| Live acceptance | [role-session-acceptance.md](../../docs/monad/role-session-acceptance.md) |
| Public evidence data | [public-evidence.json](public-evidence.json) |
| Quickstart | [quickstart.md](../../docs/monad/quickstart.md) |
| Technical video | Pending recording and actual accessible URL |
| Pitch video | Pending recording and actual accessible URL |
| Reviewer-accessible live Monad API | https://review.agentonomy.xyz/ — public wallet and MCP endpoint; fresh purchase acceptance pending |
| Track | Trust, Identity & AI Infrastructure — selected in the saved form |
| First-party open-source license | Pending owner choice; existing notices retained |

The website URLs above are supplied project links. They do not establish live
Monad execution or third-party adoption. Use the transaction links in the
acceptance record for public-chain evidence.

## Final form checklist

- Login, current field inspection and draft saving are complete. Recheck any
  final declarations and the current track rules before final submission.
  Deadline observed in the authenticated form: October 14, 11:59 GMT+8.
- Verify the deployed public wallet and MCP endpoint at
  https://review.agentonomy.xyz/ and its Monad testnet configuration. Use it for
  the live product field only with the fresh acceptance evidence; keep local
  simulated settlement clearly separate from live Monad evidence.
- Add real technical and pitch video URLs after recording. Do not use placeholder
  links or substitute simulated footage for the Monad payment.
- Confirm the first-party license choice if the current form requires an OSI
  license. Existing authorization to publish eligible code is already recorded.
- Capture purchase, delivery, transaction, feedback and history evidence before
  revoking. Then revoke from the wallet and verify that a new purchase is refused
  while at least 0.30 TestUSD remains; record any post-revoke read behavior only
  if it is observed.
- Verify the public repository and video access in a signed-out browser. Bind
  any downloaded source package to its actual Git commit and SHA-256 manifest.
- Present the completed form to the user for final review before submitting.

The X-Agent-specific `scripts/package_submission.py` and its official repository
validator are not a Monad form or eligibility validator. A saved form, source
archive or this draft is not an official hackathon submission.
