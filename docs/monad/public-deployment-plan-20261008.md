# Public Monad deployment plan — 2026-10-08

This is a bounded preparation plan for the review host. It is not a live
deployment record, registry registration proof, or public purchase attestation.
No new CREATE transaction, registration send, hosted rollout, user-wallet
signature, or two-wallet acceptance is claimed here.

## Plan identity and current evidence

The previous CREATE proposal remains a historical artifact. Retain its plan
SHA-256 as:

`694a1dfb0e4b4316f01f648e0523fd2d4851446140f6447b869cabc9bac1af0c`

A local rebuild currently reports
`ebab1edae9df825d1a1f6db914956c877051516c4b39c2e72fd839c3d35aab3a`.
That value is not a fresh future signing authorization. Any future signing
requires a newly reviewed plan, fresh two-RPC checks, and the protected
operator flow described in [the registration runbook](erc8004-registration-operator.md).

The one-time registration procedure is documented in the
[ERC-8004 registration operator runbook](erc8004-registration-operator.md).
The current local registration evidence is 40 unit tests passing with a
test-only key and mock RPCs, including race, malformed-ABI, and
nonce-changed-after-sign regressions. Those tests do not prove live RPC
agreement, contract deployment, registration, signing, broadcast, or public
hosting.

A fresh read-only registry pin proof passed through both configured RPCs at
common Verified height `69180034`, block hash
`0x583dfd95a1c0fea52c8f2c86de679b7cd03d9df1ca770ae2b954d4a2b1454a0d`,
checked at `2026-10-08T06:08:08.479090+00:00`. It confirms only the current
proxy/implementation code and binding. `agent_id` remains `null` and
registration is pending; this update records no actual signing, broadcast, or
cloud change.

The final local gates are Monad 590 and canonical Core budget 21 passing;
Commerce 25, Review 76, and Submission 6 also passed during this rollout
preparation. They do not certify public deployment or registration.

## Fixed contract creation proposal

The existing proposal keeps the two CREATE operations at nonces 3 and 4.
Their historical pins remain subject to fresh verification before any signing:

| Field | Proposed value |
| --- | --- |
| Chain | Monad testnet 10143 |
| Deployer, gas account, and payee | `0xbdcb39ac5ff83485cb35160f0ddaa0b7446ee009` |
| Execution signer | `0x968dbabb8dca19c4a8174a260cc40db66bdb7915` |
| CREATE nonces | 3 and 4 |
| Predicted TestUSD token | `0x1bf06ce9eeeb9e998cecf96cd46f1a7e5bed547a` |
| Predicted budget executor | `0x7a87b04c67c11afa7ce1a27bdb3c1c1ca55e1aa4` |
| Legacy gas price in the previous proposal | 102 gwei |
| Maximum gas per CREATE | 2,000,000 |
| Maximum total CREATE fee | 0.408 test MON |

The earlier observation at `2026-10-07T16:32:51Z` reported pending/latest
nonce 3, gas price 102 gwei, and a 28.17173121 test MON balance from both
configured RPCs. It is historical and must be refreshed immediately before
signing. It is not a nonce reservation. Any mismatch blocks execution and
requires a revised plan.

The creation bytecode pins from that proposal are:

- TestUSD: `aedc01bd819a9fc0342f0e773128ae3b33871c925e84389e758b5fd4d7e2c00e`
- Executor: `01ac39956eae3c4401e599630b864fbb7e025236e86b857ac5035fd2f3fec6ce`

The deployment planner rebuilds both transactions from local artifacts and
rejects changed artifacts, transaction fields, or journal schema. CREATE3 and
CREATE4 must be independently confirmed through both RPCs, including runtime
code and receipts, before the registration operator's optional `--rpc-check`
is used. That check is read-only and does not replace deployment evidence.

## Registration and purchase nonce budget

The public proposal registration consumes the existing relayer nonce 5. The
purchase window therefore starts at nonce 6 and ends at nonce 13: eight possible
payments. Registration is performed once by the separate operator in the
[registration runbook](erc8004-registration-operator.md), using the fixed
`register(string)` call, nonce 5, zero native value, fixed 500,000 gas, and a
gas-price cap of 150 gwei.

The maximum fee envelope remains:

| Activity | Scope | Maximum fee |
| --- | --- | ---: |
| CREATE3 and CREATE4 | Two CREATEs, up to 2,000,000 gas each | 0.408 test MON |
| ERC-8004 registration | Nonce 5, fixed 500,000 gas at 150 gwei | 0.075 test MON |
| Hosted purchases | Nonces 6 through 13, eight payments at 500,000 gas and 150 gwei | 0.600 test MON |
| Total | Creation, registration, and purchase envelope | 1.083 test MON |

The amounts are caps for this testnet review composition. TestUSD and test MON
have no monetary value in this demonstration. A nonce, payee, registry, URI,
contract address, fee, or key-scope change invalidates this proposal; do not
silently widen it.

## Operator and host gates

Only `agentonomy-commerce-review-01` in project `blockchain-nodeservice`, zone
`asia-southeast1-b`, is in scope. The public origin is
`https://review.agentonomy.xyz`; the hosted backend remains on loopback port
8092 behind the operator-managed HTTPS proxy.

The host requires separate `agentonomy-web` and `agentonomy-sign` accounts, a
root-owned release and launcher, web-owned public registry configuration, and
the protected signer boundary. The web configuration remains unreadable to the
sign account. Registration copies only non-secret network and registry pins
into a sign-owned mode `0700` operation directory with mode `0600` files.

Temporary credentials may enter the host only through the existing protected
SSH/stdin session installer. Do not use an ambient operator profile, copy
long-lived credentials, alter IAM policies, or edit Clink DEV/PROD. The
registration operator remains independent of the hosted purchase signer.

The current external access blocker is exact: the operator account
`2035629471qq@gmail.com` needs the administrator-granted
`roles/compute.osLoginExternalUser` role on the external organization for the
review VM. The missing role is organization-level; operations in this rollout
remain restricted to the review VM. Until that administrator action
is completed, do not claim that the installer, service, CREATEs, registration,
or public purchase have run remotely. Do not bypass the blocker through DEV,
instance metadata, another identity, or a copied SSH key.

## Acceptance order

1. Preserve the historical plan hash and prepare a fresh local plan from the
   reviewed artifacts. Confirm the two fixed RPC URLs and the current pending
   nonce before any signing.
2. Restore OS Login for the exact review VM, inventory the host, install the
   root-owned release, and verify the web/signer file ownership boundaries.
3. Obtain a new bounded temporary role session through the protected
   SSH/stdin installer. Verify the pinned assumed role and KMS DryRun without
   exposing credentials or generating a transaction.
4. Execute and independently verify CREATE3 and CREATE4 through both RPCs.
5. Publish the pending `/agent.json`, prepare the nonce-5 registration, and
   run the separate registration operator once. Reconcile the original hash
   and set only the proven `agent_id` after the dual-RPC identity proof.
6. Verify the public identity, receiving-wallet binding, HTTPS origin, and
   metadata. Then hand wallet signatures to the user for the finite grant,
   approve, one `0.30` TestUSD purchase, replay without a second charge,
   second-wallet isolation, and revocation checks. Optional feedback requires
   its own explicit wallet action.

Every step needs fresh evidence before it is described as complete. The old
public review endpoint remains a simulated review service until these gates
finish; local Anvil fixtures, mock RPCs, unsigned plans, and historical
observations cannot be presented as live ERC-8004 or payment evidence.

The ERC-8004 interface and event ABI reference remains the immutable official
contracts source pinned at:

<https://github.com/erc-8004/erc-8004-contracts/tree/b9e466c250744a7e06b13dff9d3c2844ed64f825>
