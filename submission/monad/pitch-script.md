# Two-minute pitch script

“An Agent can find a useful service, but giving it an unrestricted wallet is a
bad payment boundary. Agentonomy Commerce lets an Agent buy one fixed service
inside a budget a user can inspect and revoke.

Core stays in charge of wallet identity, the signed Spending Grant, policy and
risk, budget reservation, execution and audit. The user signs a bounded EIP-712
grant that fixes the token, payee, Agent scope, validity window, execution
signer, `0.50` per-payment limit and `1.00` total limit. Core then authorizes
one frozen quote and purchase ID. The ordinary ERC-20 BudgetExecutor checks the
signatures, caps, revocation and replay state. The Monad adapter adds the chain
verification path; it does not create a second wallet ledger.

We have a verifiable testnet result. On Monad chain `10143`, both contracts were
deployed and checked through two RPC observations. The owner-approved flow made
one `0.30 TestUSD` payment in block `68984424`. The payment transaction, receipt
and two-RPC proof are shown on screen. The CSV report was delivered once, and
the same order was recovered and queried again without a second payment. The
remaining budget is `0.70 TestUSD`.

That recovery behavior is the product point: if delivery fails after payment,
the system recovers the original order and result instead of charging again.
The standalone source tree runs the copied Clink Core, Marketplace and Node
flow with the necessary Monad adapter and watcher. The operator UI, API and
merchant are local loopback services; the public review service remains
simulated. No public product URL is claimed yet.

Our target users are Agent builders who buy deterministic APIs, reports or
other metered services. We have no recorded customers, revenue, partnerships,
audit or adoption claim. Multi-wallet public access, user revocation, recording and final submission
are the remaining acceptance steps.”

Keep the payment transaction and recovery evidence visible. Do not call the
already-paid order's execute path again, and do not describe TestUSD as official
USDC.
