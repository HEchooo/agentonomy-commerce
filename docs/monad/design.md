# Budget rail protocol v1

Status: implementation specification, 2026-10-03. Public-chain acceptance pending.

The existing USDC executor and simulated review application remain separate.
Core remains the authority for identity, signed business mandate, scope, risk,
reservations and audit. The budget contract adds a user-enforced ceiling. It is
not an alternative Core ledger or a general wallet.

## Exact wire format

EIP-712 domain: name `Agentonomy Budget Executor`, version `1`, actual `chainId`,
and `verifyingContract`. Solidity and Python use these exact type strings:

```
SpendGrant(bytes32 grantId,address owner,bytes32 agentScope,address token,address payee,uint256 maxPerPayment,uint256 maxTotal,uint256 validAfter,uint256 validUntil,address executionSigner)
PurchaseExecution(bytes32 grantHash,bytes32 purchaseId,bytes32 quoteHash,uint256 amount,uint256 deadline)
```

`grantHash` is the **full EIP-712 digest**, not the struct hash. Amounts are uint256
token base units, encoded as canonical unsigned decimal strings in JSON. Times
are Unix seconds. Identifiers and hashes are nonzero bytes32. Addresses are
20-byte EVM addresses. Both signatures are 65-byte ECDSA, v=27/28, low-s, nonzero
recovered signer. MVP supports EOAs, not EIP-1271 or delegated smart wallets.

The grant is signed by owner. Each execution is signed by executionSigner in
that grant, after fresh Core authorization. Anyone may relay this exact payment;
relaying cannot alter recipient, amount, order or quote. Relayer pays gas separately.

Contract API:

```
constructor(address token)
execute(SpendGrant grant, bytes ownerSignature, PurchaseExecution execution, bytes executionSignature)
revoke(bytes32 grantId)
hashGrant(SpendGrant grant) view returns (bytes32)
hashExecution(PurchaseExecution execution) view returns (bytes32)
grantDigests(address owner, bytes32 grantId) view returns (bytes32)
spent(bytes32 grantHash) view returns (uint256)
revoked(address owner, bytes32 grantId) view returns (bool)
paid(address owner, bytes32 purchaseId) view returns (bool)
```

Events (exact order/indexing):

```
PaymentExecuted(bytes32 indexed grantHash, bytes32 indexed purchaseId, address indexed owner,
                bytes32 grantId, bytes32 quoteHash, address token, address payee, uint256 amount)
GrantRevoked(address indexed owner, bytes32 indexed grantId)
```

Errors: InvalidGrant, InvalidExecution, InvalidSignature, WrongChain,
GrantRevokedError, GrantConflict, GrantNotActive, GrantExpired, ExecutionExpired,
PerPaymentExceeded, TotalExceeded, PurchaseAlreadyPaid, TransferFailed, ReentrantCall.
All are zero-argument custom errors; clients must not rely on revert text.

## Contract invariants

Immutable constructor token must contain code; immutable deployment chain must
match on every execute. Token must match grant. Nonzero owner, payee and signer;
owner != payee; nonzero IDs/scope/hashes; positive limits, per-payment <= total;
validAfter < validUntil. Valid interval is [validAfter, validUntil), execution
deadline is exclusive and must not exceed validUntil. Amount is positive.

Revocation is scoped to (msg.sender, grantId), including before first use. First
successful execution pins (owner, grantId) to grantHash. Reusing that ID with a
different body fails. spent is per full grant digest; paid is owner + purchaseId,
across grants. Effects precede transfer, guarded by non-reentrancy. All effects
and token changes revert on error. Require ERC20 transferFrom to return exactly
32 bytes encoding true, and exact owner debit and payee credit balance deltas.
Only the declared non-rebasing/non-fee test asset is supported. Balance checks
do not make an arbitrary malicious token trustworthy.

Revocation/payment races follow chain ordering. Changing signer requires a new
grant and explicit revocation of the old one. No upgrade/admin/arbitrary-call API.

## Core binding and recovery

Bind the full grant digest, owner, agent scope, chain, contract, token, payee,
limits, times and signer to one immutable version of a Core spending mandate.
Binding creation requires trusted wallet identity and verified owner signature;
untrusted MCP input cannot assign identity, mandate, signing keys or receipt.
Use the existing Core authorization, reservation and receipt path. A separate
transport/journal may store chain attempts but must never become a budget authority.

Stable purchase IDs are bytes32 commitments to the immutable canonical Core
purchase scope; quoteHash commits to frozen purchase input, quote and price.
Persist raw signed transaction/hash, execution digest and nonce before broadcast.
One attempt per owner/purchase; a crash resumes the same attempt. Unknown status
retains the reservation and cannot create a new payment or change rails.

Lifecycle: quoted -> reserved -> broadcast_pending -> payment_verified -> delivered.
payment_verified -> paid_delivery_pending retries delivery only. broadcast_pending
can enter unpaid_terminal only after independent proof of a reverted finalized
transaction; timeout, missing receipt and RPC disagreement remain pending.

Watcher validates chain ID, successful receipt, canonical block and independently
checked finality, expected executor event and token Transfer, all grant/order/
quote/owner/payee/token/amount fields. Two configured RPCs must agree. Evidence
must not expose private keys, execution credentials or raw CSV. Public testnet
requires Monad's verified execution boundary; no Polygon confirmation shortcut.

## Composition boundary

The new monad_commerce composition uses copied in-repo Core/Marketplace/Node
implementations and the real HTTP CSV merchant. It never imports another checkout
or uses the old simulated chain fixtures. Local Anvil and public Monad modes are
explicit. Test signing is restricted to local Anvil; public mode requires external
wallet and execution signing configuration and fails closed when absent.

A testnet-only first-party merchant policy must state its scope explicitly and
cannot fabricate a third-party risk result. Production risk rules stay unchanged.

Public test asset address is intentionally unspecified. A self-deployed TestUSD
is not official USDC. Local signatures/receipts are not Monad adoption evidence.
