# Dedicated KMS and external wallet acceptance record

Run date: 2026-10-04. Base: `335d47f`, branch `codex/monad-kms`.

This record covers the new external-wallet/operator composition. Public Monad
acceptance is **pending**. No public contract creation or payment was broadcast.
KMS primitive tests use fake provider responses with real secp256k1 DER
signatures; integration tests execute real contracts on local Anvil. These are
different evidence from a successful AWS `kms:Sign` request or Monad receipt.

## Verified behavior

- Wallet identity, signed Core mandate and EIP-712 budget are separate steps.
  The grant is fixed to the buyer, execution signer, chain, token and payee.
- Insufficient first allowance stays incomplete. Decimal-equivalent persisted
  terms recover after a crash; changed immutable grant terms fail closed.
- A dedicated signer process validates structured grants and transactions,
  bounds the nonce/gas/price, and never broadcasts. Ordinary runtime processes
  neither load AWS SDK credentials nor receive them over the process pipes.
- The real local external-wallet path pays 0.30 TestUSD and obtains an HTTP CSV
  result. Restart recovery uses the original purchase and relayer nonce.
- Submitted payment and paid-but-undelivered recovery survive restart. Core
  revocation still permits recovering a previously paid order without new gas
  spending or a replacement payment.
- Canonical wallet-transaction proof requires two agreeing RPC observations.
  Only proven wrong/reverted transactions permit explicit hash correction;
  missing/malformed/disagreeing/pending observations preserve the original hash.

## Fresh regression

| Scope | Result |
| --- | --- |
| Monad adapter, wallet, API/MCP, local chain and recovery | 236 passed |
| Core budget/recovery focused gate (also in Core total) | 21 passed |
| Core | 2,116 passed |
| Node | 841 passed, 1 skipped |
| Hosted | 522 passed, 4 skipped |
| Marketplace | 320 passed |
| Commerce / Review / submission package | 16 / 76 / 6 passed |
| Workspace / existing E2E | 12 / 2 passed |
| Solidity | 54 passed; invariant 256 runs, 128,000 calls |

The five skips require configured PostgreSQL test databases; this run does not
claim PostgreSQL acceptance. Initial Marketplace subprocess failures were due
to missing `python` on PATH and passed after selecting the project's virtual
environment. Framework deprecation warnings remain. Foundry could not write its
optional signature cache in the sandbox; contract tests completed successfully.
The external-wallet fixture originally collided with the Core `services`
namespace during combined collection. It now runs each real integration case
in a separate process; the formal `make PYTHON=.venv/bin/python test-monad`
entry completed successfully after the repair.

Independent review approved the signer, Core recovery and public API/UI
integration after repair of all reported findings. Four DOM/response recovery
regressions were additionally checked by the reviewer. This is an engineering
review, not an external security audit. GitNexus detected 36 changed files, 306
symbols and 26 affected flows at critical risk; the changed paths are confined
to the reviewed Commerce integration, tests and documentation. No Clink source
or production configuration was changed.

## Public prerequisites, checked separately

Both buyer and relayer had zero test MON and pending nonce zero on two RPCs.
The official faucet was left at its verification step with the buyer address
entered. No token claim succeeded. The read-only deployment plan is blocked by
insufficient funding; its two CREATE transactions have a combined maximum cost
of 0.8 test MON at the selected 200 gwei cap and 2,000,000 gas per transaction.
Payment and wallet-operation gas are additional. All quotes/nonces must be
rechecked before executing the saved plan.

The two dedicated KMS keys exist, but the operator's `kms:Sign` request was
denied. AWS also denied the approved narrow policy installation because the
operator lacks `iam:PutUserPolicy`. No policy was applied. The private
administrator handoff remains outside version control. This permission issue
is independent of temporary credential expiry or KMS key migration.

Remaining sequence: administrator enables exact-key signing, test MON arrives,
verify live KMS/public keys, review the concrete deployment scope, deploy and
check both code hashes, then have the buyer perform OKX signatures and finite
approval. Finally retain actual dual-RPC Monad payment, delivery, replay and
revocation evidence. See [the operator quickstart](kms-quickstart.md).
