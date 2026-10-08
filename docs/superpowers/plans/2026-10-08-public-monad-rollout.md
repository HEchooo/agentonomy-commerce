# Public Monad rollout implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Prepare a bounded ERC-8004 registration operator and a deployable hosted release, then verify an own-wallet public purchase when review-host access is restored.

**Architecture:** Keep the existing public purchase signer restricted to budget execution. A separate operator command may register the fixed service URI once with the already approved gas key, retaining a private at-most-once journal and independently reconciling its original hash. Reuse the existing KMS primitive, private journal writer/lock, registry adapter and wallet transaction proof.

**Tech stack:** Python 3.12, existing eth-account/eth-abi/boto3, fixed Monad testnet RPCs, systemd, GCP OS Login.

## Global constraints

- Independent Commerce repository only; no Clink DEV/PROD changes.
- Chain 10143 only; origin https://review.agentonomy.xyz; no mainnet or simulation fallback.
- Existing assumed role agentonomy-dev-kms-runtime in account 793643674201, region ap-southeast-1; execution and gas keys only.
- Credentials reach only the protected signer/operator process through the existing temporary-session installer; never Node/Core/Watcher/browser/repository.
- Registration consumes gas nonce 5 from the existing 5..13 scope; purchases retain 6..13. Do not enlarge the approved combined 1.083 test MON maximum fee.
- Zero native value, gas <=500000, gas price <=150000000000 wei, fixed Identity registry and register(string) calldata.
- No automatic rebroadcast, replacement nonce, or second signature after an attempted submission. Unknown outcomes reconcile the saved hash.
- OS Login remains enabled. GCP IAM must be repaired by an administrator before remote deployment; do not bypass it through DEV, metadata or another identity.

### Task 1: Hosted config ownership

Files: packaging/monad/agentonomy-commerce.service; docs/monad/hosted-operations.md; config-path paragraph of docs/monad/erc8004.md; new tests/monad/test_hosted_config_layout.py.

- [x] Add regression asserting the unit uses `--erc8004-config /var/lib/agentonomy-web/erc8004.json`, not the sign-user-only /etc directory.
- [x] Verify red with `python -m pytest tests/monad/test_hosted_config_layout.py -q`.
- [x] Apply the exact unit argument above and document web-owned0700 parent/config0600, retaining sign-user-only signer directory and AWS isolation.
- [x] Run the new regression plus test_hosted_server.py and test_hosted_session_installer.py; review the diff.

### Task 2: One registration, immutable original transaction

Files: new scripts/monad/erc8004_register.py; new tests/monad/test_erc8004_register.py; new docs/monad/erc8004-registration-operator.md.

Interfaces:
- `build_plan(network, registry, *, gas_price_wei) -> dict`: offline exact transaction and SHA-256 plan, with registry/network pins embedded.
- `run_registration(plan, journal_path, *, execute=False, signer_factory=None, rpc_factory=RpcClient) -> dict`: defaults to local validation; a saved attempt is read-only reconciliation; explicit execute permits one registration.
- CLI `--network-config FILE --registry-config FILE --plan FILE [--prepare --gas-price-wei INTEGER | --journal FILE [--execute --signer-config FILE]]`.

- [x] Write tests for defaults, exact scope rejection, two-RPC preflight drift, successful durable replay, timeout with no second send/sign, raw journal tampering, and registration-event/identity/reorg disagreements.
- [x] Run tests before implementation and record the expected missing-feature failure.
- [x] Implement strict plan shape/hash, fixed service owner/payee/URI/Identity registry, gas/nonce limits, private single-link journal, canonical recovered raw transaction comparison, write-ahead attempted marker, and one send.
- [x] Reuse `sign_legacy_transaction`, `_journal_lock`, `_write_atomic`, `_load_signer` with sanitized isolated AWS environment, `ERC8004Client` pins/binding/boundary checks and `verify_wallet_transaction`. The web signer interface is unchanged.
- [x] Verify exact nonce/gas/gasPrice plus Registered and ERC721 mint Transfer events through both RPCs; bind their agent_id and URI to ownerOf/getAgentWallet/tokenURI at a stable Verified boundary. Do not report complete from receipt status alone.
- [x] Run focused tests and all related Monad regressions, review independently, and document failure/recovery commands with no credential output.

### Task 3: Release and live gates

Files: deployment/registration configs and release archives under private operator storage, outside Git; current readiness docs.

- [ ] Package tracked source and contract artifacts; record SHA-256 and code pins, with no runtime state or secrets.
- [ ] Read-only recheck role identity/DryRun, chain/RPC agreement, nonce, gas, balance and registry proxy/implementation/binding. Historical observations are not fresh acceptance.
- [ ] After administrator fixes OS Login: login only to agentonomy-commerce-review-01, inventory users/units/proxy/ownership before installing the root-owned release and distinct service users.
- [ ] Obtain a new bounded temporary role session and pass it through SSH/stdin to the protected installer. Verify web cannot read credentials and the signer is the pinned assumed role before actual signing.
- [ ] Deploy CREATE nonces3/4 with the existing durable operator; verify both receipts and runtime through two RPCs. Publish pending /agent.json, register once at nonce5 with the separate operator, then set only the proven agent_id and verify public identity.
- [ ] Hand browser wallet signatures to the user: wallet login, one faucet claim, finite grant/approve/Agent install. Complete a 0.30 TestUSD purchase, report delivery and identical replay without second charge; verify second-wallet isolation and revocation. Optional feedback requires its own explicit wallet action.
- [ ] Update deployment claims/recording checklist only from live evidence. If access is still blocked, finish independent preparation and report the exact missing administrator role without claiming public completion.

Checkpoint: Tasks1/2 are implemented and independently reviewed. Final local gates: Monad590, canonical Corebudget21, Commerce25, Review76, Submission6. Offline configs and a common-boundary live read-only registry pin proof are prepared. Task3 remote steps remain blocked by the GCP provider refusal for the missing organization-level external OS Login role; no new live signature or broadcast has been made.
