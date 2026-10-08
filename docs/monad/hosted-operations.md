# Hosted Monad review operations

This runbook covers the isolated review host `agentonomy-commerce-review-01`.
It is a preparation and rotation guide, not evidence that the hosted product is
currently live. The public review deployment, contract creation, funding, and
delivery still need their own verified evidence before they are described as
live in a submission or recording.

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
that protected operator stream, using the configured GCP OS Login path; it is
not a directly runnable command until the operator has produced the bounded
AssumeRole result:

```text
<protected AssumeRole result> | /opt/homebrew/bin/gcloud compute ssh agentonomy-commerce-review-01 \
  --project=blockchain-nodeservice \
  --zone=asia-southeast1-b \
  --command='sudo /opt/agentonomy-commerce/current/.venv/bin/python -m scripts.monad.install_hosted_session'
```

Keep the project and zone explicit for every invocation. Do not change the
global gcloud configuration, switch accounts, or replace OS Login with a
copied SSH key. The command must run on the exact hostname. It accepts at most
32 KiB and prints only `status` and `expires_at` on success. A failed
validation prints a
generic error and never prints the input or an AWS exception. The current
GCP/OS Login blocker is that the operator account still needs
`roles/compute.osLoginExternalUser`; until an administrator grants that role,
do not claim that the installer or the service has been remotely run.

After the protected install, the root operator should verify the assumed-role
identity and perform the signer's configured DryRun using the protected signer
path. The AWS account, assumed-role ARN, KMS key ID/ARN, and public chain
addresses from that check are safe to report as status facts. Never report the
credential values, secret access key, session token, raw signature, or signed
transaction bytes. Keep AWS errors out of the web process. A successful DryRun
is an AWS access check only; it is not a Monad deployment or payment proof.

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
not sign or rebroadcast anything. A separately reviewed deployment plan must
provide concrete Monad creation and delivery evidence before either CREATE
transaction or a real hosted purchase is described as complete. This runbook
does not assert that either CREATE transaction, public funding, or a real
hosted purchase has occurred.
