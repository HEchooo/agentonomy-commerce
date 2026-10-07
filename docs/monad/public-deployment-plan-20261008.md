# Public review deployment proposal — 2026-10-08

This is a concrete **unsigned, unbroadcast** proposal. It preserves the previous
contracts and delivered order. No new contract or public service is claimed live.

## New contract creation

Plan SHA-256:
`694a1dfb0e4b4316f01f648e0523fd2d4851446140f6447b869cabc9bac1af0c`.

| Field | Proposed value |
| --- | --- |
| Chain | Monad testnet 10143 |
| Deployer/gas/payee | `0xbdcb39ac5ff83485cb35160f0ddaa0b7446ee009` |
| Execution signer | `0x968dbabb8dca19c4a8174a260cc40db66bdb7915` |
| CREATE nonces | 3 and 4 |
| Predicted TestUSD faucet | `0x1bf06ce9eeeb9e998cecf96cd46f1a7e5bed547a` |
| Predicted budget executor | `0x7a87b04c67c11afa7ce1a27bdb3c1c1ca55e1aa4` |
| Legacy gas price | 102 gwei |
| Maximum gas per CREATE | 2,000,000 |
| Maximum total CREATE fee | 0.408 test MON |

At `2026-10-07T16:32:51Z`, both configured RPCs returned pending/latest nonce 3,
gas price 102 gwei and a relayer balance of 28.17173121 test MON. This timestamped
observation must be refreshed immediately before signing; it is not a future
nonce reservation. Any mismatch blocks execution and requires a revised plan.

Creation bytecode SHA-256 pins:

- Faucet: `aedc01bd819a9fc0342f0e773128ae3b33871c925e84389e758b5fd4d7e2c00e`
- Executor: `01ac39956eae3c4401e599630b864fbb7e025236e86b857ac5035fd2f3fec6ce`

The Faucet has no constructor arguments, creates 1000 valueless TestUSD in its
own pool, and permits each address to claim 1.00 once. It has no mint, admin,
pause or upgrade surface. The Executor constructor binds exactly to the predicted
Faucet. The planner rebuilds both transactions from these local artifacts and
rejects a changed plan or historical journal schema.

A new journal's default dry-run validates locally without AWS or RPC calls.
An existing pending/complete journal may perform read-only two-RPC reconciliation;
it does not sign or rebroadcast an unknown transaction.

## Public runtime scope

After both CREATEs are independently verified, propose a separate purchase scope:

- Same approved DEV assumed role in `ap-southeast-1`, and the previously
  verified execution/gas keys. Exact AWS account, role and key identifiers stay
  in the private operator configuration; no third or production key is in scope.
- Exact new token/executor, payee, RPCs and chain 10143. Wallet owner is the only
  field that may vary, after website proof and signed finite authorization.
- Relayer nonces 5 through 13 inclusive: at most nine purchase transactions.
- Maximum 500,000 gas and 150 gwei per purchase: total fee cap 0.675 test MON.
- Combined deployment and purchase fee cap: 1.083 test MON. TestUSD and test MON
  have no monetary value in this demonstration.

If deployment nonces change, this proposal's predicted addresses and purchase
window are invalid. Do not silently widen the scope. The guarded launcher accepts
only an owner change from its installed pins; it cannot be used for CREATE.

## Host and acceptance

Only the independent `agentonomy-commerce-review-01` in project
`blockchain-nodeservice`, zone `asia-southeast1-b`, is in scope. HTTPS origin is
`https://review.agentonomy.xyz`; the backend listens on loopback 8092. Two separate
OS users, a root-owned release/launcher and a protected temporary role-session
installer are required. See [hosted operations](hosted-operations.md).

Current status: local role/key pins and KMS DryRun passed without creating a
signature; GCP OS Login remains blocked; no new CREATE, server rollout, user
wallet signature or two-wallet live acceptance has been performed. Preserve
these distinctions in submission text and recordings.
