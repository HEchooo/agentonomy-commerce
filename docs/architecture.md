# Architecture and source boundary

Agentonomy Commerce is an independent export of the Clink working tree, focused
on reliable service procurement. The internal `clink_node` name and existing
Core contracts are retained so that authorization and financial rules are not
rewritten during migration.

Agent → unified Node MCP → Marketplace → Core → payment execution/verification.
Marketplace requests authorization and settlement from Core, then requests and
returns the service result. Core owns identity, signed grants, policy/risk,
reservation accounting and audit. Marketplace cannot grant itself a budget.

`apps/core`, `apps/marketplace`, `apps/node`, `apps/facilitator` and `contracts`
contain the complete first-party implementation. `apps/prediction-markets` is
retained for compatibility with existing Node imports and regression contracts;
it is not the Commerce demo's product focus. Generic Linux installer code is
retained. Private production deployment assets, user-specific acceptance
fixtures, databases, credentials and Git history are excluded.

`docs/source-manifest.json` records hashes at export time; subsequent Commerce
changes are tracked by this repository's Git history. It is provenance for the
export, not a claim that every later file still has its initial hash.

The demo is a local rehearsal, not evidence of a live blockchain transaction.
External chain/risk/service fixtures must be explicit and isolated from the
production implementation. Production wallet keys are never part of this repo.

## Authorization handoff correction

Commerce corrects one imported Marketplace integration gap: after Core resolves
an active signed grant, `PurchaseService.execute` now passes Core's explicit
`user_interaction_required` decision as the policy request's
`requires_confirmation`. Only the boolean `false` permits silent purchase;
missing or invalid values continue to require confirmation. It does not invent
`user_confirmed=true`, bypass risk decisions or replace Funding's fresh
signature, mandate, allowance and budget checks.

The local MCP test demonstrates a signed `silent_under_limits` grant purchasing
without per-purchase confirmation. Six focused regression cases cover the
handoff, including fail-closed handling of unknown values.
