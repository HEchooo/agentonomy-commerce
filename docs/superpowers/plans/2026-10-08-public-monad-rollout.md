# Public Monad rollout implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Prepare a bounded ERC-8004 registration operator and a deployable hosted release, then verify an own-wallet public purchase after the remaining review-host and chain gates pass.

**Architecture:** Keep the existing public purchase signer restricted to budget execution. A separate operator command may register the fixed service URI once with the already approved gas key, retaining a private at-most-once journal and independently reconciling its original hash. Reuse the existing KMS primitive, private journal writer/lock, registry adapter and wallet transaction proof.

**Tech stack:** Python 3.12, existing eth-account/eth-abi/boto3, fixed Monad testnet RPCs, systemd, and instance-level GCP Compute Engine SSH access for the review VM.

## Global constraints

- Independent Commerce repository only; no Clink DEV/PROD changes.
- Chain 10143 only; origin https://review.agentonomy.xyz; no mainnet or simulation fallback.
- Existing assumed role agentonomy-dev-kms-runtime in account 793643674201, region ap-southeast-1; execution and gas keys only.
- Credentials reach only the protected signer/operator process through the existing temporary-session installer; never Node/Core/Watcher/browser/repository.
- Registration consumes gas nonce 5 from the existing 5..13 scope; purchases retain 6..13. Do not enlarge the approved combined 1.083 test MON maximum fee.
- Zero native value, gas <=500000, gas price <=150000000000 wei, fixed Identity registry and register(string) calldata.
- No automatic rebroadcast, replacement nonce, or second signature after an attempted submission. Unknown outcomes reconcile the saved hash.
- Use the authorized current bounded instance-level SSH public key registration only on `agentonomy-commerce-review-01` in project `blockchain-nodeservice`, zone `asia-southeast1-b`; keep its private key in the protected operator environment, never on the VM, repository, or service accounts. Keep project and zone explicit, reject inherited project-level keys with `block-project-ssh-keys=TRUE`, and leave Clink DEV/PROD untouched. The instance metadata switch to `enable-oslogin=FALSE` and `block-project-ssh-keys=TRUE` has been applied, and SSH validation is complete: the successful session returned the exact hostname `agentonomy-commerce-review-01`. The existing `agentonomy-commerce-iap-ssh` rule only allowed TCP/22 from `35.235.240.0/20`, which explains why public direct SSH was not allowed. A separate local IAP HTTPS attempt reset; its network cause is unverified. The temporary `agentonomy-commerce-review-ssh-temp` rule allows TCP/22 only from `45.77.70.37/32` and targets the uniquely tagged `commerce-review-admin` review VM; delete it after deployment. The prior OS Login attempt was denied on 2026-10-08 because the operator account lacked `roles/compute.osLoginExternalUser` on the external organization; that historical denial was specific to the old login method and is not a current blocker for the authorized instance-level path. Public rollout and chain steps remain pending.
- The `64dc2c7` release is root-owned on the review host; `agentonomy-web` and `agentonomy-sign` are isolated by distinct UIDs. The web account cannot read `/etc/agentonomy-commerce/signer.json` or `/var/lib/agentonomy-sign/aws/credentials`, and the sign account cannot read `/var/lib/agentonomy-web/erc8004.json`. The protected installer established the bounded session for `arn:aws:sts::793643674201:assumed-role/agentonomy-dev-kms-runtime/commerce-review-20261008`, expiring `2026-10-08T18:39:35+00:00`; its session policy permits only `DescribeKey`, `GetPublicKey`, and `Sign` on the two pinned KMS keys. Role identity, public-key checks, and both KMS DryRuns passed, but this is not payment success. The new systemd service is active on `127.0.0.1:8092` behind HTTPS with HTTP-to-HTTPS redirect; the old simulated Docker container is stopped with data retained, startup-script metadata was removed, and old unit/Caddy backups are under `/root/agentonomy-commerce-rollout-backup-20261008`. Public `/agent.json` returns CSV Reconciliation metadata with `active=false`; the service is unregistered. The first CREATE hash is `0x1c7f7a6d4506e87f1572952fff740837b338451ab63063594f5424996e495d67`; primary-RPC receipt success is recorded, dual-RPC verification is pending. The source finalized-head comparison fix passed the Monad 592 and Core 21 regression gates; deploy the new release and reverify the original journal before acceptance. Nonce 4 and nonce 5 are not broadcast, wallet steps remain pending, and the temporary `agentonomy-commerce-review-ssh-temp` rule remains active only for deployment closeout and must then be deleted.

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
- [x] With SSH validation complete: connect only to agentonomy-commerce-review-01 with the authorized instance-level public key, inventory users/units/proxy/ownership, and install the root-owned release with distinct service users.
- [x] Obtain a bounded temporary role session and pass it through SSH/stdin to the protected installer. Verify web cannot read credentials and the signer is the pinned assumed role before actual signing; role identity, public-key checks, and both KMS DryRuns passed.
- [ ] Deploy CREATE nonces3/4 with the existing durable operator; verify both receipts and runtime through two RPCs. Publish pending /agent.json, register once at nonce5 with the separate operator, then set only the proven agent_id and verify public identity.
- [ ] Hand browser wallet signatures to the user: wallet login, one faucet claim, finite grant/approve/Agent install. Complete a 0.30 TestUSD purchase, report delivery and identical replay without second charge; verify second-wallet isolation and revocation. Optional feedback requires its own explicit wallet action.
- [ ] Update deployment claims/recording checklist only from live evidence. Keep the public rollout and chain steps unclaimed until the remaining host, contract, registration, and wallet acceptance gates have fresh evidence.

Checkpoint: Tasks1/2 are implemented and independently reviewed. Final local gates: Monad592, canonical Corebudget21, Commerce25, Review76, Submission6. Offline configs and a common-boundary live read-only registry pin proof are prepared. The review VM SSH path, root-owned `64dc2c7` release, account isolation, protected session installation, KMS DryRuns, and active HTTPS-backed systemd service are verified. The temporary `agentonomy-commerce-review-ssh-temp` rule is limited to `45.77.70.37/32` and must be deleted after deployment. The first CREATE has only primary-RPC receipt evidence; the source finalized-head comparison fix passed Monad592/Core21, and the new release plus original-journal re-verification are pending. Nonce 4, nonce 5, registration, public purchase, and wallet acceptance remain pending, and no completed chain rollout is claimed.
