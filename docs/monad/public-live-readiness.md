# Own-wallet public Monad product readiness

Status on 2026-10-08 (Asia/Shanghai): the new hosted release is deployed on
the dedicated review VM and its ERC-8004 registration is complete. The service
runs as an active systemd unit on `127.0.0.1:8092` behind Caddy at
`https://review.agentonomy.xyz`; the external HTTPS identity probe now passes.
The user-wallet purchase and downstream acceptance steps are still pending. This
document therefore records public deployment and identity evidence, not a
completed public purchase.

The previous accepted one-wallet Monad order and its contracts remain useful
historical evidence, but they do not replace acceptance of this new public
hosted flow. See the [public rollout acceptance record](public-rollout-acceptance-20261008.md)
and [role-session acceptance](role-session-acceptance.md).

## Verified rollout state

- Release `44fdd9fb0d228c2b6b58d4673e8ecb54bf09ba76` is installed only on
  `agentonomy-commerce-review-01` in project `blockchain-nodeservice`, zone
  `asia-southeast1-b`, with observed address `34.21.234.127`.
- Instance metadata has `enable-oslogin=FALSE` and
  `block-project-ssh-keys=TRUE`. The existing eight-hour instance public key
  is restricted to this review VM and expires at `2026-10-08T14:21:05Z`. The
  temporary single-IP SSH firewall rule was removed; only the original
  `agentonomy-commerce-iap-ssh` TCP/22 rule from `35.235.240.0/20` targeting
  `commerce-review-admin` remains.
- `agentonomy-web` and `agentonomy-sign` are separate service users. The web
  user cannot read AWS credentials or signer configuration. `agentonomy-web` is
  active/running with its backend listener only at `127.0.0.1:8092`; Caddy is
  the HTTPS front end. The legacy simulated Docker container is not running,
  its data is retained, and its startup script was removed.
- The independent bounded KMS role session was verified for the pinned
  execution and gas keys. Assumed-role identity, public-key checks and both
  signing dry runs passed; its policy is limited to `DescribeKey`,
  `GetPublicKey` and `Sign`. This is signer-boundary evidence, not payment
  evidence.

The fresh external HTTPS probe verified the certificate and returned HTTP 200
for `/agent.json` with `active=true` and Agent ID `2073`. The
`/.well-known/agent-registration.json` response matched the same registration
identity. The hosted wallet page returned HTTP 200 with the expected connect and
OPC-approve identifiers, CSP and `Cache-Control: no-store`; anonymous
`/api/status` returned `401`. A fresh browser page had no console errors or
warnings. These checks establish endpoint reachability and metadata only; they
do not establish a wallet consent or purchase.

The current release includes the finalized-head comparison fix. The latest
rollout gates recorded for that release are 592 Monad tests in 45.11 seconds and
21 canonical Core budget tests in 2.34 seconds. The earlier 64 contract, 25
Commerce, 76 Review, six Submission, 108 Core OPC and 121 Node OPC/MCP counts
remain previously recorded gates; they are not represented here as a fresh
full rerun.

## Deployed contracts and registration

Both CREATE journals are `complete`. Runtime configuration, contract state,
constructors, receipts and finality were independently checked through both
Monad RPCs at their recorded boundaries.

| Artifact | Nonce | Address | CREATE transaction | Mined block | Runtime hash | Verified boundary |
| --- | ---: | --- | --- | ---: | --- | ---: |
| TestUSD | 3 | `0x1bf06ce9eeeb9e998cecf96cd46f1a7e5bed547a` | [`0x1c7f7a6d4506e87f1572952fff740837b338451ab63063594f5424996e495d67`](https://testnet.monadexplorer.com/tx/0x1c7f7a6d4506e87f1572952fff740837b338451ab63063594f5424996e495d67) | 69187111 | `0xe587fc80f9f476b07b58fdef987df555b1612d81ff66a5be800bdf695dc9c167` | 69189452 |
| Budget executor | 4 | `0x7a87b04c67c11afa7ce1a27bdb3c1c1ca55e1aa4` | [`0xb31a1954f60a2947bbeb6f67c84fbe481bf3968e20249640238e76a1de360e41`](https://testnet.monadexplorer.com/tx/0xb31a1954f60a2947bbeb6f67c84fbe481bf3968e20249640238e76a1de360e41) | 69189585 | `0x6bf7d58744627ed56b89134b6ce354981f4638a74a8338f9b0376c488bdb70d2` | 69189718 |

The ERC-8004 registration used nonce 5 and was sent once:

- [Registration transaction](https://testnet.monadexplorer.com/tx/0x039583372a8e324da28e1e8ed278ee7d57967235bae2496b9bd91bcd9592a2f0)
  `0x039583372a8e324da28e1e8ed278ee7d57967235bae2496b9bd91bcd9592a2f0`,
  mined in block `69189943`.
- Canonical registration status is `complete`, Agent ID `2073`, finality block
  `69191573`, canonical hash
  `0x32648ee0d7297e399da3f57cb294a8b6be4f20839ed29e75518a546903137bea`.
- Identity verification at block `69191587` binds owner, gas payee and
  receiving wallet `0xbdcb39ac5ff83485cb35160f0ddaa0b7446ee009` to
  `https://review.agentonomy.xyz/agent.json` in the fixed Identity registry
  `0x8004a818bfb912233c491871b3d84c89a494bd9e`.
- The web-owned registry configuration stores `agent_id=2073`; its `agent.json`
  endpoint returns `active=true`,
  `registrations=[agentId=2073,agentRegistry=eip155:10143:0x8004a818bfb912233c491871b3d84c89a494bd9e]`,
  `x402Support=false` and `supportedTrust=[reputation]`. The same identity was
  verified through the external HTTPS endpoint. The endpoint metadata does not
  carry owner or receiving-wallet fields; those come from the independent
  registration proof above. The official Reputation registry is
  `0x8004b663056a597dffe9eccc1965a193b7388713`.

No Clink DEV/PROD service, IAM policy, KMS Key Policy, mainnet authorization or
production payment configuration changed in this rollout.

## Implemented flow

1. Connect an EOA browser wallet on Monad testnet 10143. Sign a fresh wallet
   challenge bound to the HTTPS domain and the browser/CSRF session.
2. Claim 1.00 valueless TestUSD from the fixed-supply claim contract. Users
   supply test MON for their own claim, approve and revoke transactions.
3. Authorize the existing Core business grant, sign its EIP-712 chain budget,
   and approve exactly 1.00 TestUSD. The grant lasts one day, has a 1.00 total
   limit and a 0.50 per-payment limit. Login never renews or resets it.
4. Sign installation consent for this wallet's hosted demonstration Agent.
   Canonical OPC exchanges a short-lived credential inside the hosted process;
   the user does not copy a token. Each MCP admission and callback rechecks Core.
5. Discover and preview the CSV reconciliation service through the shipped
   Node and actual MCP SDK, then purchase for 0.30 TestUSD. Core retains identity,
   policy, reservation, funding and audit authority. Marketplace retains order
   idempotency, verified payment and paid-delivery recovery.
6. Query or recover the same order, then demonstrate Core and chain revocation
   with distinct states. A delivery failure does not authorize another payment.

This release implements the website-hosted Agent flow. An externally installed
Agent's publicly reachable pairing/MCP endpoint is not part of this rollout.

## Isolation and recovery

- Wallet proof determines the server-owned opaque tenant; clients cannot select
  a user, tenant, Agent or merchant. Each wallet has separate Core/Marketplace
  state and its own hosted installation.
- Browser credentials are Secure HttpOnly cookies. State stores browser/CSRF
  digests; no AWS credential or Agent token appears in a browser response.
- One durable relayer gate spans all wallets. Unknown transaction outcomes
  retain their original order across restarts. Only canonical Core evidence
  verified by both RPCs can release a submitted payment's lane.
- Re-login preserves the original identity, grant, counters, allowance and
  orders. An unsigned grant challenge may move to a newly verified browser;
  signed or expired authorization is never silently reconstructed.
- The browser preserves only the current wallet's original preview, purchase
  and idempotency references. It restores them only after fresh server
  authentication; a refresh or timed-out response never automatically executes,
  signs or broadcasts a replacement payment.
- Linux uses `agentonomy-web` and `agentonomy-sign`. The root-installed launcher
  accepts only a canonical owner change from its pinned configuration. Keys,
  role, network, credential path, nonce window and gas limits remain exact.
  The release and virtual environment are root-owned and immutable to both
  service accounts. Local process isolation alone is not an OS security boundary.

## Bounded review deployment

The application admits at most 128 active browser sessions, 32 persistent wallet
namespaces and four open wallet runtimes. This is a supervised hackathon trial,
not an unlimited multiuser production service. The claim pool contains exactly
1000 TestUSD, with one 1.00 claim per address and no mint/admin/upgrade capability.
The application admission limit is separate from the contract's claim pool.

The public relayer purchase window is exactly nonces 6 through 13: eight
purchase transactions after registration nonce 5. Extending that window requires
a separately reviewed scope update. Each buyer grant remains bounded to 1.00
TestUSD total, 0.50 per payment and one day. Failed or exhausted authorization
does not create a replacement grant or wallet budget.

## Remaining live gates

- Have buyer wallet `0x59899831691aa79507818961773497c751bffc8b` perform a fresh
  HTTPS login, one 1.00 TestUSD claim, business budget and EIP-712 grant signing,
  finite 1.00 allowance, and hosted OPC installation consent.
- Complete one 0.30 CSV purchase, delivery and same-order recovery without a
  second payment. Then capture Core and chain revoke/refusal evidence.
- Verify a second wallet cannot read or use the first wallet's budget or orders.
  Optional ERC-8004 feedback requires its own explicit wallet action. Recording
  and final submission remain pending until these live results exist.

The previous local one-wallet payment is preserved in the recording checklist as
historical evidence. It does not satisfy any of the public wallet gates above.
