# Public Monad deployment plan — 2026-10-08

This is a bounded rollout record and remaining acceptance plan for the review
host. It records complete evidence for the two CREATE journals, the ERC-8004
registration, and the final public HTTPS probe, but it is not a public
purchase attestation. The host release and service are installed, and the
identity promotion is complete; hosted purchase, user-wallet signatures, and
two-wallet acceptance remain pending.

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

A pre-registration read-only registry pin proof passed through both configured
RPCs at common Verified height `69180034`, block hash
`0x583dfd95a1c0fea52c8f2c86de679b7cd03d9df1ca770ae2b954d4a2b1454a0d`,
checked at `2026-10-08T06:08:08.479090+00:00`. It confirms only the current
proxy/implementation code and binding. Its `agent_id` was `null` because this
was before registration; it records no actual signing, broadcast, or chain
deployment. Current registration evidence is recorded below.

The final local gates are Monad 592 and canonical Core budget 21 passing;
Commerce 25, Review 76, and Submission 6 also passed during this rollout
preparation. They do not certify public deployment or registration.

## Fixed contract creation proposal

The existing proposal keeps the two CREATE operations at nonces 3 and 4.
Their historical pins remain subject to fresh verification before any further
signing:

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

The currently deployed release is
`44fdd9fb0d228c2b6b58d4673e8ecb54bf09ba76`; its source has passed the Monad 592
and Core 21 regression gates. The TestUSD CREATE at nonce 3 uses token
`0x1bf06ce9eeeb9e998cecf96cd46f1a7e5bed547a` and transaction
`0x1c7f7a6d4506e87f1572952fff740837b338451ab63063594f5424996e495d67`,
mined in block `69187111` at Verified boundary `69189452`. The executor CREATE
at nonce 4 uses executor
`0x7a87b04c67c11afa7ce1a27bdb3c1c1ca55e1aa4` and transaction
`0xb31a1954f60a2947bbeb6f67c84fbe481bf3968e20249640238e76a1de360e41`,
mined in block `69189585` at Verified boundary `69189718`. Both journals were
reverified through runtime, state, and receipts on both RPCs.

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

For this rollout, nonce 5 was signed and broadcast once as transaction
`0x039583372a8e324da28e1e8ed278ee7d57967235bae2496b9bd91bcd9592a2f0`.
The latest read-only canonical proof reports `complete`, Agent ID `2073`,
registration block `69189943`, finality block `69191573`, and canonical hash
`0x32648ee0d7297e399da3f57cb294a8b6be4f20839ed29e75518a546903137bea`.
Owner/wallet `0xbdcb39ac5ff83485cb35160f0ddaa0b7446ee009` and URI
`https://review.agentonomy.xyz/agent.json` were identity-verified at block
`69191587`. The root operator then atomically changed the web-owned registry
config from `agent_id=null` to `agent_id=2073` and restarted the service. The
loopback `/agent.json` reports `active=true`,
`registrations=[agentId=2073, agentRegistry=eip155:10143:0x8004a818bfb912233c491871b3d84c89a494bd9e]`,
`name=Agentonomy CSV Reconciliation`, `x402Support=false`, and
`supportedTrust=[reputation]`. The external HTTPS probe completed with
certificate validation enabled: `GET /agent.json` returned 200 with
`active=true` and Agent ID `2073` bound to the expected registry;
`/.well-known/agent-registration.json` matched this metadata; `/` returned 200
with the new hosted-wallet UI and connect/OPC-approve controls; CSP and
`Cache-Control: no-store` were present; anonymous `GET /api/status` returned
401; and the browser page had no errors or warnings. The frozen plan and
original signed transaction nonces/hashes remained unchanged; CREATE proof
journals were updated by reconciliation, the registration journal was not
modified, and no duplicate broadcast occurred.

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

The `44fdd9fb0d228c2b6b58d4673e8ecb54bf09ba76` release is deployed in a
separate root-owned tree. The two
service accounts use distinct UIDs; `agentonomy-web` cannot read
`/etc/agentonomy-commerce/signer.json` or
`/var/lib/agentonomy-sign/aws/credentials`, while `agentonomy-sign` cannot
read the web-owned registry configuration. The active systemd service listens
on `127.0.0.1:8092` behind the HTTPS proxy, and HTTP redirects to HTTPS. The
legacy Docker container is stopped with its data retained; old
startup-script metadata was removed, and old unit/Caddy backups are under
`/root/agentonomy-commerce-rollout-backup-20261008`.

The protected SSH/stdin installer installed the bounded session for
`arn:aws:sts::793643674201:assumed-role/agentonomy-dev-kms-runtime/commerce-review-20261008`,
which expires at `2026-10-08T18:39:35+00:00`. Its session policy permits only
`DescribeKey`, `GetPublicKey`, and `Sign` on the two pinned KMS keys. The role,
public-key checks, and both KMS DryRuns passed; this does not establish payment
success. The root operator atomically changed the web-owned registry config
from `agent_id=null` to `agent_id=2073` and restarted the service. The loopback
`/agent.json` returns the CSV Reconciliation metadata with `active=true`,
`registrations=[agentId=2073, agentRegistry=eip155:10143:0x8004a818bfb912233c491871b3d84c89a494bd9e]`,
`name=Agentonomy CSV Reconciliation`, `x402Support=false`, and
`supportedTrust=[reputation]`. The external HTTPS probe is complete as recorded
above; this is public endpoint evidence only.

Temporary credentials may enter the host only through the existing protected
SSH/stdin session installer. Use the authorized instance-level SSH public key
registered for this VM, with the project and zone explicit on every command.
The instance metadata is `enable-oslogin=FALSE` and
`block-project-ssh-keys=TRUE`, so inherited project-level keys are excluded.
The existing `jefffeng` instance key registration is valid for 8h. Keep the
private key only in the protected operator environment; never copy it
to the host, repository, or service accounts. Do not use an ambient operator
profile, copy long-lived credentials, alter IAM policies, or edit Clink DEV/PROD.
The registration operator remains independent of the hosted purchase signer.

Switching this review VM from OS Login to its instance-level SSH public key was
explicitly authorized and the instance metadata has been applied. SSH
validation is complete: the successful session returned the exact hostname
`agentonomy-commerce-review-01`. The existing
`agentonomy-commerce-iap-ssh` rule allows TCP/22 only from
`35.235.240.0/20`, which explains why public direct SSH was not allowed. A
separate local IAP HTTPS attempt reset; its network cause is unverified. The
temporary `agentonomy-commerce-review-ssh-temp` rule was deleted successfully
after deployment. The remaining `agentonomy-commerce-iap-ssh` rule is the only
TCP/22 rule for this target, limited to source `35.235.240.0/20` and target
`commerce-review-admin`; public direct TCP/22 remains disallowed. This verifies
host access, protected installation, service activation, both CREATE journal
re-verifications, canonical registration, loopback identity promotion, and the
external HTTPS probe. Public purchase and user-wallet actions remain pending.
For historical context, the prior OS Login attempt on 2026-10-08 was denied
because the existing operator account lacked the
administrator-granted `roles/compute.osLoginExternalUser` role on the external
organization. That denial was specific to the previous OS Login method and is
not a current blocker for the authorized instance-level path.

## Acceptance order

1. Preserve the historical plan hash and prepare a fresh local plan from the
   reviewed artifacts. Confirm the two fixed RPC URLs and the current pending
   nonce before any signing.
2. Use the authorized instance-level SSH public key on the exact review VM,
   with `enable-oslogin=FALSE` and `block-project-ssh-keys=TRUE` applied only
   there. With SSH validation complete, inventory the host, install the
   root-owned release, and verify the web/signer file ownership boundaries.
3. Obtain a new bounded temporary role session through the protected
   SSH/stdin installer. Verify the pinned assumed role and KMS DryRun without
   exposing credentials or generating a transaction.
4. Completed: deploy the current release containing the finalized-head
   comparison fix, reverify the nonce-3 journal, and independently verify the
   nonce-4 CREATE through both RPCs. Runtime, state, and receipts passed at the
   recorded Verified boundaries; do not rebroadcast either transaction.
5. Completed: run the separate registration operator once at nonce 5 and
   reconcile its original hash. The canonical CLI reports Agent ID `2073` and
   the owner, wallet, URI, and finality evidence recorded above; the frozen
   plan and original signed transaction nonces/hashes remained unchanged,
   CREATE proof journals were updated by reconciliation, the registration
   journal was not modified, and no duplicate broadcast occurred.
6. The final public HTTPS probe and public identity re-verification are
   complete after the loopback promotion. Then hand the required wallet actions to the user: EIP-191 login,
   one TestUSD claim, business-budget/EIP-712 finite approve, OPC consent, one
   `0.30` TestUSD purchase, replay without a second charge, second-wallet
   isolation, revocation, feedback, and video checks. The old local one-wallet
   delivery is historical evidence only and cannot substitute for this public
   flow.

Every step needs fresh evidence before it is described as complete. The current
systemd-backed review service is deployed, while public purchase and wallet
acceptance remain open; local Anvil fixtures, mock RPCs, unsigned plans,
and historical observations cannot be presented as live ERC-8004 or payment
evidence.

The ERC-8004 interface and event ABI reference remains the immutable official
contracts source pinned at:

<https://github.com/erc-8004/erc-8004-contracts/tree/b9e466c250744a7e06b13dff9d3c2844ed64f825>
