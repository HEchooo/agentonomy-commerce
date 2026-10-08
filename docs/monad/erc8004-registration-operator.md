# ERC-8004 registration operator

This document describes one bounded, operator-only ERC-8004 registration on
Monad testnet. It is a preparation and recovery procedure. It is not evidence
that the public service, registry identity, or hosted purchase is live.

The registration command is separate from the hosted purchase signer. It is
never reachable from the web service and it does not enlarge the purchase
signing scope. The service owner and gas payee are the fixed address
`0xbdcb39ac5ff83485cb35160f0ddaa0b7446ee009`.

## Fixed registration boundary

The current `scripts/monad/erc8004_register.py` accepts only this public
rollout scope:

| Field | Fixed value or rule |
| --- | --- |
| Chain | Monad testnet, chain ID `10143` |
| RPC 1 | `https://testnet-rpc.monad.xyz` |
| RPC 2 | `https://rpc-testnet.monadinfra.com` |
| Origin | `https://review.agentonomy.xyz` |
| Agent URI | `https://review.agentonomy.xyz/agent.json` |
| Service owner, registration sender, and network payee | `0xbdcb39ac5ff83485cb35160f0ddaa0b7446ee009` |
| Identity registry | `0x8004a818bfb912233c491871b3d84c89a494bd9e` |
| Reputation registry | `0x8004b663056a597dffe9eccc1965a193b7388713` |
| TestUSD token pin | `0x1bf06ce9eeeb9e998cecf96cd46f1a7e5bed547a` |
| Budget executor pin | `0x7a87b04c67c11afa7ce1a27bdb3c1c1ca55e1aa4` |
| Calldata | `register(string)` on the fixed Identity registry |
| Native value | `0` |
| Nonce | `5` |
| Gas limit | Fixed `500000` |
| Gas price | Positive and at most `150000000000` wei (`150` gwei) |

The plan starts with `agent_id: null`. The operator must not choose a registry,
owner, payee, URI, nonce, or transaction recipient from a request. Any network,
RPC, token, executor, registry, owner, URI, or fee-boundary drift blocks the
plan. A gas price below the 150 gwei cap is valid; the selected value is frozen
in the plan and checked again before the send.

The current event ABI reference is the immutable official ERC-8004 contracts
source already used by this repository:

<https://github.com/erc-8004/erc-8004-contracts/tree/b9e466c250744a7e06b13dff9d3c2844ed64f825>

The reconciliation path expects the pinned Identity `Registered(uint256,string,address)`
event and the matching ERC-721 mint `Transfer(address,address,uint256)` event,
then verifies the resulting owner, URI, receiving wallet, code pins, and
two-RPC verified boundary. A receipt status alone is not registration proof.

## Host and operator separation

The root operator creates a private operation directory owned by
`agentonomy-sign`, for example `/var/lib/agentonomy-sign/erc8004-registration`.
The directory must be mode `0700`. The copied `network-config.json`,
`registry-config.json`, frozen plan, journal, and lock are regular single-link
files owned by `agentonomy-sign` with mode `0600`.

Only non-secret network and registry data may be copied into this directory.
The web configuration remains in the web-owned tree at
`/var/lib/agentonomy-web` with mode `0600`; `agentonomy-sign` must not read it
directly. Root may copy the reviewed public network and registry pins into the
sign-user-owned operation directory, then invoke the command as
`agentonomy-sign`. Do not copy credentials, session tokens, private keys,
transaction bytes, or browser material.

The protected signer scope remains
`/etc/agentonomy-commerce/signer.json`, owned by `agentonomy-sign` with mode
`0600` in a mode `0700` parent. The temporary AWS session remains under
`/var/lib/agentonomy-sign/aws`. The existing temporary-session installer is
the only supported credential path: a bounded AssumeRole result is passed over
the protected SSH/stdin flow. Do not use an ambient operator profile, a
long-lived key, an environment-injected secret, or a copied credential file.
Do not change IAM, Clink DEV/PROD, the hosted purchase signer, or the fixed
KMS key scope as part of registration.

## Exact CLI modes

The script requires `--network-config FILE`, `--registry-config FILE`, and
`--plan FILE` in every mode. It also exposes `--prepare`, `--rpc-check`,
`--gas-price-wei INTEGER`, `--journal FILE`, `--execute`, and
`--signer-config FILE`.

### Prepare a frozen plan

Run the read-only plan mode as `agentonomy-sign`:

```text
python -m scripts.monad.erc8004_register \
  --network-config /var/lib/agentonomy-sign/erc8004-registration/network-config.json \
  --registry-config /var/lib/agentonomy-sign/erc8004-registration/registry-config.json \
  --plan /var/lib/agentonomy-sign/erc8004-registration/registration.plan.json \
  --prepare \
  --gas-price-wei <positive value at most 150000000000>
```

`--prepare` rejects `--execute`, `--signer-config`, and `--journal`. It writes
an exact transaction and SHA-256 `plan_hash` atomically, with
`signed: false` and `broadcast: false`. An existing plan path, including a
symlink, is rejected instead of being replaced.

`--rpc-check` is optional only in `--prepare` mode. It performs read-only
checks against both fixed RPCs for chain, latest and pending nonce, gas price,
balance, and gas estimate. Operationally, use it only after CREATE nonce 3
and nonce 4 have been independently deployed and verified and the relayer's
nonce 5 is valid. The registration transaction keeps the fixed `500000` gas
limit; the fee remains a ceiling based on that limit and the selected gas
price. A matching response from the two RPCs is still not a live registration
result.

### Validate or reconcile without credentials

With a frozen plan, status mode requires `--journal` and forbids
`--gas-price-wei` and `--rpc-check`:

```text
python -m scripts.monad.erc8004_register \
  --network-config /var/lib/agentonomy-sign/erc8004-registration/network-config.json \
  --registry-config /var/lib/agentonomy-sign/erc8004-registration/registry-config.json \
  --plan /var/lib/agentonomy-sign/erc8004-registration/registration.plan.json \
  --journal /var/lib/agentonomy-sign/erc8004-registration/registration.journal.json
```

This mode validates the plan and configuration without loading a signer. If an
existing journal has `attempted: true`, it reconciles the original transaction
hash and does not need credentials. A pending journal therefore must be
checked this way after an uncertain send; do not create a new plan or journal.

### Explicit one-time execution

Only an operator may opt into signing and sending, and the signer path must be
explicit:

```text
python -m scripts.monad.erc8004_register \
  --network-config /var/lib/agentonomy-sign/erc8004-registration/network-config.json \
  --registry-config /var/lib/agentonomy-sign/erc8004-registration/registry-config.json \
  --plan /var/lib/agentonomy-sign/erc8004-registration/registration.plan.json \
  --journal /var/lib/agentonomy-sign/erc8004-registration/registration.journal.json \
  --execute \
  --signer-config /etc/agentonomy-commerce/signer.json
```

The command never signs or broadcasts by default. `--execute` requires the
protected signer configuration and uses the fixed gas signer scope. It does
not accept arbitrary calldata, a replacement nonce, a second signer, or an
ambient AWS profile. A signer configuration without `--execute` is rejected.

## Journal and uncertain outcomes

The journal parent is mode `0700`. The journal is a regular single-link mode
`0600` file, and its lock is also mode `0600`. The journal binds the raw signed
legacy transaction and its hash to the frozen plan. Those transaction bytes
are immutable; the only state transition is the durable write-ahead
`attempted` marker from `false` to `true`.

The execution sequence is fixed:

1. Run the two-RPC preflight before signing.
2. Sign the frozen nonce-5 transaction with the isolated gas signer.
3. Atomically save the raw transaction and hash with `attempted: false`.
4. Run the preflight again after signing.
5. Atomically set `attempted: true` before the RPC send.
6. Send exactly once and reconcile that same original hash.

If the send times out, returns an error, or otherwise leaves the result
uncertain, keep the journal and report `pending`. A later status run reads the
same hash and independently verifies the original transaction, receipt,
events, finality, identity, and both RPC observations. It never signs again,
rebroadcasts, replaces the nonce, or charges a second time. If the original
evidence disagrees or cannot be verified, the result is `blocked`; preserve the
journal for investigation.

The operator flow is independent of the hosted purchase signer. A successful
registration does not grant spending authority, change the Core budget, or
make a hosted purchase valid without the existing identity, policy, budget,
execution, watcher, and audit checks.

## Current evidence boundary

The current local registration gate is 40 unit tests passing with a test-only
private key and mock RPCs, including race, malformed-ABI, and nonce-changed-
after-sign regressions. That is local behavior evidence only; it is not a
Monad registration, live RPC agreement, signer run, broadcast, or public
deployment.

A fresh read-only registry pin proof passed through both configured RPCs at
common Verified height `69180034`, block hash
`0x583dfd95a1c0fea52c8f2c86de679b7cd03d9df1ca770ae2b954d4a2b1454a0d`,
checked at `2026-10-08T06:08:08.479090+00:00`. This confirms only the current
proxy/implementation code and binding. `agent_id` remains `null` and
registration is pending; no signing, broadcast, or cloud change is implied.

The final local gates are Monad 590 and canonical Core budget 21 passing;
Commerce 25, Review 76, and Submission 6 also passed during this rollout
preparation. They do not certify public deployment. No live claim may be made until the protected
host rollout, CREATE verification, nonce-5 registration, public identity
verification, and independent wallet purchase acceptance all have fresh
evidence.
