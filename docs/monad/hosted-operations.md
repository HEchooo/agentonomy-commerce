# Hosted Monad review operations

This runbook covers the isolated review host `agentonomy-commerce-review-01`.
It is a preparation and rotation guide, not evidence that the hosted product is
fully live or accepted. The review host release and service are now installed
and verified, while contract finality, registration, funding, delivery, and
wallet acceptance still need their own evidence before they are described as
complete in a submission or recording.

## Verified rollout state — 2026-10-08

The `64dc2c7` release is installed in a separate root-owned tree on the review
host. `agentonomy-web` and `agentonomy-sign` use distinct UIDs. The web account
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
with the HTTPS proxy in front; HTTP requests redirect to HTTPS. The old
simulated Docker container is stopped and its data is retained. The old
startup-script metadata was removed to prevent rollback, and the old unit and
Caddy configuration are backed up under
`/root/agentonomy-commerce-rollout-backup-20261008`.

The public `/agent.json` currently returns the CSV Reconciliation metadata with
`active=false`; the service is not registered. The first CREATE original
transaction hash is
`0x1c7f7a6d4506e87f1572952fff740837b338451ab63063594f5424996e495d67`.
At least the primary RPC reports a successful receipt, but formal dual-RPC
verification is pending. The source fix for the dynamic finalized-head
comparison bug has passed the Monad 592 and Core 21 regression gates; the new
release must be deployed and the original journal reverified before that
acceptance can finish. Nonce 4 and nonce 5 have not been broadcast, and the
user-wallet steps remain pending.

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
so inherited project-level SSH keys are not accepted. Keep the corresponding
private key only in the protected operator environment; never copy it to the
VM, repository, or service accounts. Do not change the global gcloud
configuration, switch accounts, or alter any other host; Clink DEV and PROD
remain untouched. SSH connectivity is now verified: the successful session
returned the exact hostname `agentonomy-commerce-review-01`. The existing
`agentonomy-commerce-iap-ssh` firewall rule allows TCP/22 only from
`35.235.240.0/20`, which explains why public direct SSH was not allowed. A
separate local IAP HTTPS attempt reset; its network cause is unverified. The temporary
`agentonomy-commerce-review-ssh-temp` rule now allows TCP/22 only from
`45.77.70.37/32` and is bound only to the uniquely tagged
`commerce-review-admin` review VM. Delete this temporary rule after the
deployment; it remains active only for this deployment closeout and is not a
permanent access path. Host access, protected installation, and service start
are verified, but the public rollout and chain actions remain pending.

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
not sign or rebroadcast anything. The first CREATE listed above has only
primary-RPC receipt evidence so far; dual-RPC finality is pending. The source
fix for the finalized-head comparison bug passed the Monad 592 and Core 21
regression gates; deploy the new release and reverify the original journal
before accepting it. Nonce 4 and registration nonce 5 have not been
broadcast, and no public funding, hosted purchase, or wallet acceptance is
claimed here.
