# Hosted Monad review operations

This runbook covers the isolated review host `agentonomy-commerce-review-01`.
It is a preparation and rotation guide, not evidence that the hosted product is
fully live or accepted. The review host release and service are now installed
and verified. Both CREATE journals, the ERC-8004 registration, and the final
public HTTPS probe have current evidence, while funding, delivery, and
user-wallet acceptance still need their own evidence before they are described
as complete in a submission or recording.

## Verified rollout state — 2026-10-08

The `44fdd9fb0d228c2b6b58d4673e8ecb54bf09ba76` release is deployed in a
separate root-owned tree on the review host. `agentonomy-web` and
`agentonomy-sign` use distinct UIDs. The web account
cannot read `/etc/agentonomy-commerce/signer.json` or
`/var/lib/agentonomy-sign/aws/credentials`, and the sign account cannot read
the web-owned registry configuration. The protected SSH/stdin installer
installed the bounded session for
`arn:aws:sts::793643674201:assumed-role/agentonomy-dev-kms-runtime/commerce-review-20261008`,
which expires at `2026-10-08T18:39:35+00:00`. Its session policy is limited to
`DescribeKey`, `GetPublicKey`, and `Sign` on the two pinned KMS keys. The role
identity, public-key checks, and both KMS signing DryRuns passed; this is an AWS
access result and is not payment success.

The new systemd service is active as `agentonomy-web` on `127.0.0.1:8092`,
with the HTTPS proxy in front; HTTP requests redirect to HTTPS. The legacy
Docker container is stopped and its data is retained. The old
startup-script metadata was removed to prevent rollback, and the old unit and
Caddy configuration are backed up under
`/root/agentonomy-commerce-rollout-backup-20261008`.

The root operator atomically changed the web-owned registry config from
`agent_id=null` to `agent_id=2073` and restarted the service. The loopback
`/agent.json` now returns the CSV Reconciliation metadata with `active=true`,
`registrations=[agentId=2073, agentRegistry=eip155:10143:0x8004a818bfb912233c491871b3d84c89a494bd9e]`,
`name=Agentonomy CSV Reconciliation`, `x402Support=false`, and
`supportedTrust=[reputation]`. The external HTTPS probe completed with
certificate validation enabled: `GET /agent.json` returned 200 with
`active=true` and Agent ID `2073` bound to the expected registry;
`/.well-known/agent-registration.json` matched this metadata; `/` returned 200
with the new hosted-wallet UI and connect/OPC-approve controls; CSP and
`Cache-Control: no-store` were present; anonymous `GET /api/status` returned
401; and the browser page had no errors or warnings. The current release has
passed the Monad 592 and Core 21 regression gates. The TestUSD CREATE at nonce 3 uses token
`0x1bf06ce9eeeb9e998cecf96cd46f1a7e5bed547a` and transaction
`0x1c7f7a6d4506e87f1572952fff740837b338451ab63063594f5424996e495d67`,
mined in block `69187111` at Verified boundary `69189452`. The executor CREATE
at nonce 4 uses executor
`0x7a87b04c67c11afa7ce1a27bdb3c1c1ca55e1aa4` and transaction
`0xb31a1954f60a2947bbeb6f67c84fbe481bf3968e20249640238e76a1de360e41`,
mined in block `69189585` at Verified boundary `69189718`. Both journals were
reverified through runtime, state, and receipts on both RPCs. The nonce-5
ERC-8004 registration was signed and broadcast once as transaction
`0x039583372a8e324da28e1e8ed278ee7d57967235bae2496b9bd91bcd9592a2f0`;
the latest read-only canonical proof reports `complete`, Agent ID `2073`,
registration block `69189943`, finality block `69191573`, and canonical hash
`0x32648ee0d7297e399da3f57cb294a8b6be4f20839ed29e75518a546903137bea`.
Owner/wallet `0xbdcb39ac5ff83485cb35160f0ddaa0b7446ee009` and URI
`https://review.agentonomy.xyz/agent.json` were identity-verified at block
`69191587`. The frozen plan and original signed transaction nonces/hashes
remained unchanged; CREATE proof journals were updated by reconciliation, the
registration journal was not modified, and no duplicate broadcast occurred.
New public wallet EIP-191 login, TestUSD claim, business-budget and
EIP-712 finite approve, OPC consent, purchase, feedback, revoke, two-wallet,
and video checks remain pending user actions; the old local one-wallet delivery
is historical evidence only and cannot substitute for them.

## Boundaries

The web service runs as `agentonomy-web` and listens only on
`127.0.0.1:8092`. An external HTTPS proxy may publish
`https://review.agentonomy.xyz`, but it must preserve that origin and must not
expose the signer files. The service has no AWS environment variables. It asks
the fixed launcher to perform an isolated signer operation through the
`agentonomy-sign` account.

The signer profile is fixed to the `agentonomy-commerce-monad-role` profile in
`ap-southeast-1`. The protected session is limited to the assumed role pin in
the private `signer.json`; its role name is
`agentonomy-dev-kms-runtime`. The installer accepts only an `ASIA...`
temporary access key and an expiration more than one hour and at most twelve
hours plus five minutes from installation.
The installer does not accept a long-lived key, change IAM or KMS policy, or
start a service.

## Host layout

Use two non-root service accounts:

* `agentonomy-web` owns the hosted application state and systemd process.
* `agentonomy-sign` owns `/var/lib/agentonomy-sign/aws`, which is mode `0700`.

The web-owned state/config parent is `/var/lib/agentonomy-web`, and it must be
mode `0700`. Keep `configuration.json`, the `state/` directory, and the public
ERC-8004 registry pins in this tree. Store the registry pins at
`/var/lib/agentonomy-web/erc8004.json` as a regular single-link file owned by
`agentonomy-web` with mode `0600`. It contains public registry, Agent, owner,
URI, and code-hash pins only; it must not contain credentials, session tokens,
private keys, or transaction signing material. The hosted server's protected
reader relies on this ownership and mode and rejects links or other unsafe
filesystem objects.

The release at `/opt/agentonomy-commerce/current` and its virtual environment
are root-owned and not writable by either service account. Install the fixed
launcher at `/usr/local/libexec/agentonomy-monad-sign` as root. The launcher
must run the checked-out release signer with a clean environment and the fixed
Python path; it must not accept command arguments.

The non-secret signer scope is stored at
`/etc/agentonomy-commerce/signer.json`. Keep the directory owned by
`agentonomy-sign` and mode `0700`, and the file owned by `agentonomy-sign` with
mode `0600`, so only the dedicated signer can read or change this fixed scope.
The release installer and web user must not be able to rewrite it. It contains
the fixed account, region, KMS key ARNs, and scope only. Do not put a
credential, session token, private key, or raw transaction in this file.

Before starting the service, the root operator must verify that both config
files are regular single-link files with no group or other permissions:
`/var/lib/agentonomy-web/erc8004.json` is owned by `agentonomy-web` and mode
`0600`, while `/etc/agentonomy-commerce/signer.json` is owned by
`agentonomy-sign` and mode `0600`. The two parent directories remain mode
`0700`. A check as `agentonomy-web` must be able to read the web-owned public
registry config and must not read the protected signer scope. A check as
`agentonomy-sign` must be able to read its protected signer scope and must not
read the web-owned registry config; do not widen either directory or file
permission to make the other account's config readable.

The installer writes only these files under
`/var/lib/agentonomy-sign/aws`:

* `credentials` with the fixed AWS profile and temporary values;
* `config` with the fixed profile and region;
* `session-info.json` containing only the assumed role ARN and expiration.

The directory and files are checked for real, single-link filesystem objects.
Changed contents are copied into a root-owned `0700` directory under
`/root/agentonomy-commerce-session-backups`, with backup files mode `0600`.

## Protected installation

Run the existing protected AWS operator flow on the review host. It must invoke
the new installer through stdin and keep the AssumeRole JSON out of logs,
shell history, chat, and the repository. The following is a placeholder for
that protected operator stream, using the authorized instance-level SSH public
key path on this VM; it is not a directly runnable command until the operator
has produced the bounded AssumeRole result:

```text
<protected AssumeRole result> | /opt/homebrew/bin/gcloud compute ssh agentonomy-commerce-review-01 \
  --project=blockchain-nodeservice \
  --zone=asia-southeast1-b \
  --command='cd /opt/agentonomy-commerce/current && sudo /opt/agentonomy-commerce/current/.venv/bin/python -m scripts.monad.install_hosted_session'
```

Keep the project and zone explicit for every invocation and run the command on
the exact hostname. This host is authorized to use its current bounded
instance-level SSH public key registration, matching the DEV access pattern.
Root has applied
`enable-oslogin=FALSE` and `block-project-ssh-keys=TRUE` to this instance only,
so inherited project-level SSH keys are not accepted. The existing `jefffeng`
instance key registration is valid for 8h. Keep the corresponding
private key only in the protected operator environment; never copy it to the
VM, repository, or service accounts. Do not change the global gcloud
configuration, switch accounts, or alter any other host; Clink DEV and PROD
remain untouched. SSH connectivity is now verified: the successful session
returned the exact hostname `agentonomy-commerce-review-01`. The existing
`agentonomy-commerce-iap-ssh` firewall rule allows TCP/22 only from
`35.235.240.0/20`, which explains why public direct SSH was not allowed. A
separate local IAP HTTPS attempt reset; its network cause is unverified. The
temporary `agentonomy-commerce-review-ssh-temp` rule was deleted successfully
after deployment. The remaining `agentonomy-commerce-iap-ssh` rule is the only
TCP/22 rule for this target, limited to source `35.235.240.0/20` and target
`commerce-review-admin`; public direct TCP/22 remains disallowed. Host access,
protected installation, service start, both CREATE journal re-verifications,
canonical registration, loopback identity promotion, and the external HTTPS
probe are verified. Hosted purchase and user-wallet actions remain pending.

For historical context, the prior OS Login attempt on 2026-10-08 was denied
because the operator account lacked the administrator-granted
`roles/compute.osLoginExternalUser` role on the external organization. That
denial applied to the previous OS Login method and is not a current blocker for
the authorized instance-level path.

The protected install has been completed and the assumed-role identity and
signer's configured DryRun passed through the protected signer path. The AWS
account, assumed-role ARN, KMS key ID/ARN, and public chain addresses from that
check are safe to report as status facts. Never report credential values,
secret access keys, session tokens, raw signatures, or signed transaction
bytes. Keep AWS errors out of the web process. A successful DryRun is an AWS
access check only; it is not a Monad deployment or payment proof.

## Service start and checks

Review the rendered unit before enabling it. It must keep `User=agentonomy-web`,
`UMask=0077`, `ProtectHome=true`, `Restart=on-failure`, the fixed loopback
address, and the fixed `review.agentonomy.xyz` origin. Do not add arbitrary
sudo commands, `SETENV`, AWS variables, or a shell escape to the sudoers rule.
The rule permits only `agentonomy-web` to invoke the no-argument signer launcher
as `agentonomy-sign`.

Before exposing the HTTPS proxy, verify as root that `agentonomy-web` cannot
read `/var/lib/agentonomy-sign/aws/credentials`, that the signer can read its
own profile, and that the web process has no AWS credential environment. Check
the service logs for sanitized startup and DryRun status only. The browser
origin, TLS certificate, and proxy forwarding rules are deployment controls;
the application itself remains bound to loopback.

## Rotation and failure handling

Rotate by obtaining a fresh bounded AssumeRole result and running the same
root-only installer. It atomically replaces changed files and leaves a root
backup. Do not copy the operator profile, long-lived access keys, or raw AWS
credentials to the host. Do not modify Clink DEV or PROD policies, KMS key
selection, contract permissions, or payment switches during rotation.

If the session is expired, malformed, on the wrong role, or the protected
paths have unsafe ownership, stop and repair the host boundary. Do not bypass
the installer or infer validity from a role name. A failed or unknown signer
operation remains an unknown payment state and must be reconciled through the
canonical Core evidence path; it must not trigger a second charge.

For a new faucet deployment, an absent journal defaults to a plan-only/DryRun
path and does not call RPC, sign, or broadcast. An existing journal may be
checked through the read-only RPC reconciliation path; that reconciliation does
not sign or rebroadcast anything. The nonce-3 and nonce-4 CREATE journals
listed above were reverified through runtime, state, and receipts on both RPCs.
The source finalized-head comparison fix passed the Monad 592 and Core 21
regression gates, and the current release is deployed. The nonce-5
registration was broadcast once and its canonical status is complete; the
frozen plan and original signed transaction nonces/hashes remained unchanged;
CREATE proof journals were updated by reconciliation, the registration journal
was not modified, and no duplicate broadcast occurred. No public
funding, hosted purchase, or user-wallet acceptance is claimed here. New
public EIP-191 login, TestUSD claim, business-budget/EIP-712 finite approve,
OPC consent, purchase, feedback, revoke, second-wallet, and video checks still
require the user.
