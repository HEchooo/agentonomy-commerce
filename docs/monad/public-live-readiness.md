# Public Monad product readiness — wallet and OPC proposal

Status on 2026-10-07: design preparation, not a public deployment. The user has
chosen **reviewers use their own wallets**. The user then asked to discuss the
website/OPC binding and temporary-token flow. No wallet-authentication or
signer-scope change is implemented by this document.

## Confirmed current boundary

- Actual Monad testnet contracts and one paid/delivered order are recorded in
  [role-session-acceptance.md](role-session-acceptance.md).
- `examples/monad_commerce/public_api.py` is a fixed-owner loopback operator
  adapter. Its random session cookie is not wallet authentication. It cannot
  be made a multi-wallet public product by adding only a reverse proxy.
- `PublicCanary` / `PublicCoreBridge` / `ExternalWalletCore` reuse the copied
  Core, Marketplace and Node business services. The signer is pinned to one
  configured owner and nonce scope; each current state directory belongs to
  that owner.
- `review.agentonomy.xyz` remains the simulated public review deployment.
  It is not the required live Monad product endpoint.
- Read-only GCP inventory confirms the previously authorized independent
  `agentonomy-commerce-review-01` is RUNNING, e2-medium, at 34.21.234.127 in
  asia-southeast1-b, project blockchain-nodeservice. This is an inventory check,
  not proof of installed live runtime, HTTPS routes or available OS access.

## Identity model to reuse

The copied Core already has account browser sessions and OPC installation
pairing. These are two forms of access to the same Core authority, not two
payment engines:

| Identity | Existing implementation | Intended public role |
| --- | --- | --- |
| User wallet | AccountService wallet challenge/verification | Prove wallet control on the website |
| Browser | Account browser session and CSRF cookie checks | View own identity, limits, allowance and orders |
| OPC installation | OpcAccountService pairing plus wallet-authorized installation clause | Bind a particular Agent/device to an active spending grant |
| Agent credential | OpcAccountService.issue_token / authenticate_access_token | Short-lived capability for the bound installation |
| Signing service | Dedicated KMS signer worker | Authorize only a Core-approved purchase, with no AWS credential export |

The OPC device uses an **ES256 JWT proof** to obtain a short-lived bearer.
The user wallet separately signs the installation consent with **EIP-191**.
This is not Hosted Facilitator DPoP. The existing OPC token TTL is
**300 seconds**, bounded further by installation
consent and grant expiry. A pairing expires after **10 minutes**. These are
source facts in `apps/core/services/account_service/opc_service.py`; they are
not a claim that the current Monad operator app already exposes OPC.

The copied OPC code currently binds `agent_id=hermes` and payments scope. The
Monad acceptance composition has its own fixed Agent/owner configuration.
Their identity and grant relationship must be deliberately integrated and
tested. Do not replace this binding with an arbitrary browser-supplied user ID
or hand a generic bearer token to an unverified visitor.

## Proposed user flow

1. Open the project's actual HTTPS commerce page and connect a compatible EOA
   wallet on Monad testnet 10143.
2. Sign a fresh, domain-bound wallet-login challenge. The server resolves the
   verified account and establishes an HttpOnly Secure browser session. Merely
   returning an address from `eth_requestAccounts` is not login proof.
3. Inspect and authorize the service scope, token, merchant, total limit,
   per-payment limit and expiry. Keep business Mandate and EIP-712 onchain grant
   distinct, with a finite ERC-20 approve. Never request a wallet private key.
4. For an external Agent, open the OPC installation's one-time binding link and
   authorize that named installation against the same wallet and Mandate. The
   Agent obtains/refreshes its short-lived credential through the existing
   installation proof mechanism; the user does not copy a backend token.
5. Search, quote and buy through the existing unified `clink_node` MCP.
   Marketplace/Core/contract/watcher perform the same verified payment and
   delivery flow. Each account sees only its own grants, orders and results.
6. Re-query/recover the paid order without another debit. Revoke Agent consent,
   Core Mandate and the chain grant with their separate states shown clearly.

For a browser-only hackathon experience, a hosted Agent can perform the
MCP call after the website has bound its installation to the verified user.
The reviewer should not need to install an Agent or copy a temporary token.
That hosted Agent admission and consent must be real OPC/Core authorization;
it is not permission to reuse the fixed demo installation across wallets.
An external Agent can use the separate pairing flow above. This is a proposed
product choice, not yet implemented or accepted on the public endpoint.


The website is the user control surface. Browser-session, Agent-access and AWS
STS credentials have different recipients and expiry rules; none substitutes
for a valid wallet spending signature or chain allowance.

## Implementation gates before exposing the service

- Integrate the copied Core account/OPC routes with the Monad composition,
  including exact account-to-owner and installation-to-grant binding. Keep
  production chain allowlists and Clink DEV/PROD untouched.
- Persist separate account budget/order state, authorize every read and write
  by the server-resolved account, and test cross-wallet access refusal. Reload
  or expiry must not reset a budget or silently replace a grant.
- Serialize the shared relayer nonce path across accounts. An unknown broadcast
  must retain its original hash and block unsafe replacement, including after
  restart. Existing fixed-owner nonce scope must not be casually widened.
- Resolve TestUSD availability for a new wallet and separate test MON gas
  acquisition. The deployed `AgentonomyTestUSD` has only **1.00 TestUSD fixed
  total supply**, initially assigned to the accepted buyer. It has transfer,
  approve and transferFrom, but **no mint/faucet**. A new wallet cannot buy
  simply by logging in. The `mint` in local mocks is not deployed. Choose a
  bounded test-token distribution/recycling design or a separately reviewed
  test faucet deployment before advertising self-service funding; do not
  silently replace accepted token/executor addresses or widen signer scope.
- Serve the public origin over TLS; keep Core, Marketplace, merchant and signer
  private. Use exact Origin/Host checks, CSRF, session expiry, body limits and
  bounded admission. Do not proxy the existing anonymous operator session.
- Run signing under an independent protected identity. Install only the approved
  role's short-lived session through the protected installer and SSH/stdin;
  never copy operator long-term access keys or give AWS credentials to Node,
  Core, Marketplace, Watcher or browsers.
- Verify two real wallets have independent identity/grants/orders; prove one
  actual testnet purchase/delivery/recovery and revoke/refusal. Then publish the
  actual HTTPS URL and update the saved form, before recording final footage.

## Existing files to reuse / anticipated adaptation

- `apps/core/services/account_service/{app,service,repository,opc_service}.py`:
  browser login, consent and installation/token authority.
- `apps/node/clink_node/opc_{client,core_client,onboarding,setup}.py` and shared
  OPC protocol: installation proof and unified Agent access.
- `examples/monad_commerce/{public_api,public_canary,public_core,public_bridge,
  public_worker,public_market_worker,public_node}.py`: verified Monad composition
  currently used for single-wallet acceptance.
- `agentonomy_commerce/{budget_backend,budget_network,signer_process}.py`:
  payment rail, independent observations and isolated signing.

This is a reuse map and acceptance boundary, not permission to edit all these
modules. Freeze the concrete interfaces and run symbol impact analysis before
assigning disjoint implementation tasks. Add failing behavioral tests for
wallet/session replay, cross-account access and relayer concurrency before
changing runtime code.
