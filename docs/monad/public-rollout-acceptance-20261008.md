# Public Monad rollout acceptance — 2026-10-08

This record captures the verified public deployment and ERC-8004 identity
evidence available on 2026-10-08 (Asia/Shanghai). The new release is installed
on the dedicated review VM, both new contract deployments are independently
verified through two Monad RPCs, and the ERC-8004 registration is complete.
The external HTTPS identity probe and certificate check now pass, while the buyer-wallet purchase
flow and downstream acceptance remain pending. This record does not claim public
purchase, recovery, revocation, second-wallet, feedback or video acceptance.

No raw signed transactions, wallet signatures, AWS session credentials,
browser tokens, private keys, seed phrases or runtime secrets are included here.
Transaction hashes, contract addresses and public metadata are the only chain
evidence recorded.

## Deployment status

| Gate | Status | Evidence |
| --- | --- | --- |
| Root-owned release | Complete | `44fdd9fb0d228c2b6b58d4673e8ecb54bf09ba76` on `agentonomy-commerce-review-01` |
| Target scope | Complete | Project `blockchain-nodeservice`, zone `asia-southeast1-b`, observed VM address `34.21.234.127` |
| Service process | Complete | Separate `agentonomy-web`/`agentonomy-sign` users; active/running web unit on `127.0.0.1:8092` behind Caddy HTTPS |
| Legacy service cleanup | Complete | Old simulated Docker container stopped with data retained; startup script removed |
| External HTTPS probe | Complete | `/agent.json` and `/.well-known/agent-registration.json` returned matching active Agent 2073 metadata |
| Buyer-wallet acceptance | Pending | Fresh login, claim, budget, purchase, recovery and remaining user actions |

Instance metadata is `enable-oslogin=FALSE` and
`block-project-ssh-keys=TRUE`. The existing eight-hour instance public key was
used only for this review VM and expires at `2026-10-08T14:21:05Z`. The
temporary single-IP SSH firewall rule was removed; only the original
`agentonomy-commerce-iap-ssh` TCP/22 rule from `35.235.240.0/20` targeting
`commerce-review-admin` remains.

The independent KMS signer boundary is installed and verified. The bounded
assumed role is
`arn:aws:sts::793643674201:assumed-role/agentonomy-dev-kms-runtime/commerce-review-20261008`,
with the recorded session expiry `2026-10-08T18:39:35Z`. Its scope is limited
to `DescribeKey`, `GetPublicKey` and `Sign` on the pinned execution and gas
keys. Role identity, public-key checks and both signing dry runs passed. The
web user cannot read AWS credentials or signer configuration. No Clink DEV/PROD,
IAM policy, KMS Key Policy, mainnet or production payment change was made.

The fresh external probe verified the HTTPS certificate and returned HTTP 200 for `/agent.json` with `active=true`
and Agent ID `2073`. The well-known registration document matched the same
identity. The hosted wallet page returned HTTP 200 with the expected connect and
OPC-approve identifiers, CSP and `Cache-Control: no-store`; anonymous
`/api/status` returned `401`. A fresh browser page had no console errors or
warnings. These checks establish endpoint reachability and metadata only; they
do not establish wallet consent or a purchase.

## Contract proof

Both canonical deployment journals report `complete`. Runtime configuration,
contract state, constructors, receipts, code hashes and finality were checked
through both RPCs at the recorded verified boundaries. The release contains the
fixed sequential-moving-finalized-head comparison; no new Solidity source
change was introduced for this rollout.

| Contract | Nonce | Address | CREATE transaction | Mined block | Runtime hash | Verified boundary |
| --- | ---: | --- | --- | ---: | --- | ---: |
| TestUSD | 3 | `0x1bf06ce9eeeb9e998cecf96cd46f1a7e5bed547a` | [`0x1c7f7a6d4506e87f1572952fff740837b338451ab63063594f5424996e495d67`](https://testnet.monadexplorer.com/tx/0x1c7f7a6d4506e87f1572952fff740837b338451ab63063594f5424996e495d67) | 69187111 | `0xe587fc80f9f476b07b58fdef987df555b1612d81ff66a5be800bdf695dc9c167` | 69189452 |
| Budget executor | 4 | `0x7a87b04c67c11afa7ce1a27bdb3c1c1ca55e1aa4` | [`0xb31a1954f60a2947bbeb6f67c84fbe481bf3968e20249640238e76a1de360e41`](https://testnet.monadexplorer.com/tx/0xb31a1954f60a2947bbeb6f67c84fbe481bf3968e20249640238e76a1de360e41) | 69189585 | `0x6bf7d58744627ed56b89134b6ce354981f4638a74a8338f9b0376c488bdb70d2` | 69189718 |

The latest rollout gates for this release are `592 Monad` tests in `45.11s` and
`21 Core` budget tests in `2.34s`. Earlier contract, Commerce, Review,
Submission, Core OPC and Node OPC/MCP gate counts remain previously recorded
evidence; this document does not present them as a fresh full rerun.

## ERC-8004 identity proof

The registration operator used the fixed Identity registry
`0x8004a818bfb912233c491871b3d84c89a494bd9e`, fixed URI
`https://review.agentonomy.xyz/agent.json`, and nonce `5`. It signed and
broadcast exactly once:

- [Registration transaction](https://testnet.monadexplorer.com/tx/0x039583372a8e324da28e1e8ed278ee7d57967235bae2496b9bd91bcd9592a2f0):
  `0x039583372a8e324da28e1e8ed278ee7d57967235bae2496b9bd91bcd9592a2f0`,
  mined in block `69189943`.
- Canonical proof status: `complete`; Agent ID `2073`; finality block
  `69191573`; canonical hash
  `0x32648ee0d7297e399da3f57cb294a8b6be4f20839ed29e75518a546903137bea`.
- Identity verification at block `69191587` binds owner, gas payee and
  receiving wallet `0xbdcb39ac5ff83485cb35160f0ddaa0b7446ee009` to the fixed
  URI. The official Reputation registry is
  `0x8004b663056a597dffe9eccc1965a193b7388713`.
- After the read-only registration proof completed, the root operator promoted
  only the web-owned registry configuration to `agent_id=2073`. Loopback
  `agent.json` reports `active=true`,
  `registrations=[agentId=2073,agentRegistry=eip155:10143:0x8004a818bfb912233c491871b3d84c89a494bd9e]`,
  `x402Support=false` and `supportedTrust=[reputation]`. The same identity is
  now verified through the external HTTPS endpoint.

## Pending public wallet acceptance

The buyer wallet is
`0x59899831691aa79507818961773497c751bffc8b`. It must complete a fresh
HTTPS EIP-191 login, claim exactly 1.00 TestUSD, sign the business budget and
EIP-712 grant, set a finite 1.00 TestUSD allowance, and sign hosted OPC
installation consent. The grant is one day, with a 1.00 TestUSD total limit and
a 0.50 maximum per payment.

After those prerequisites, the acceptance flow is one 0.30 TestUSD CSV purchase
through the unified `clink_node` MCP, verified delivery, and same-order
recovery after refresh or restart without a second payment. The public relayer
window is exactly purchase nonces `6` through `13`, eight transactions after
registration nonce `5`.

The external HTTPS identity and metadata gate is complete. The remaining gates
are:

- buyer purchase, delivery and same-order recovery;
- Core and onchain revoke/refusal evidence;
- second-wallet isolation from the first wallet's budget and orders;
- optional buyer feedback with its own explicit wallet action; and
- recording and final submission with actual video URLs.

The prior local one-wallet payment
[`0x2711ae051fbf132e544e984c11db6b3b8b518caa40a70d380520743159408037`](https://testnet.monadexplorer.com/tx/0x2711ae051fbf132e544e984c11db6b3b8b518caa40a70d380520743159408037)
used earlier contracts and an earlier order. It remains historical delivery and
recovery evidence and does not satisfy these new public gates.
