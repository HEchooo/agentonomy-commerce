# Monad KMS administrator handoff

This public document describes the handoff process without publishing cloud
account, key, or operator identifiers. The exact values and approved policy
are kept in the ignored local operator artifact
`.artifacts/monad-kms/administrator-handoff.md`; the root agent can use that
file when preparing the durable private handoff.

The current integration uses an independently assumed runtime role, verified on
2026-10-07. This supersedes the earlier proposal to add direct signing permission
to the operator IAM user. Do not apply that historical user policy or attach the
runtime's explicit-deny policy to an operator, user group or production role.

The operator may bootstrap a temporary session of the approved role. The
signing process receives only that independently stored temporary session; it
must not use the operator profile, a `source_profile` chain, or copied long-term
access keys. DEV and Commerce use different session names and credential files.
The test keys may be shared with DEV; isolation from PROD does not imply
isolation between those two test consumers.

Apply the following *session policy* when assuming the existing approved role,
using the exact two key ARNs from the private handoff. It restricts Commerce
further than the role's three-key DEV allowance. It does not grant permissions
that the role itself lacks:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadCommerceTestKeys",
      "Effect": "Allow",
      "Action": [
        "kms:DescribeKey",
        "kms:GetPublicKey"
      ],
      "Resource": [
        "arn:aws:kms:<AWS_REGION>:<AWS_ACCOUNT_ID>:key/<KEY_ID_1>",
        "arn:aws:kms:<AWS_REGION>:<AWS_ACCOUNT_ID>:key/<KEY_ID_2>"
      ]
    },
    {
      "Sid": "SignCommerceTestDigests",
      "Effect": "Allow",
      "Action": "kms:Sign",
      "Resource": [
        "arn:aws:kms:<AWS_REGION>:<AWS_ACCOUNT_ID>:key/<KEY_ID_1>",
        "arn:aws:kms:<AWS_REGION>:<AWS_ACCOUNT_ID>:key/<KEY_ID_2>"
      ],
      "Condition": {
        "StringEquals": {
          "kms:SigningAlgorithm": "ECDSA_SHA_256",
          "kms:MessageType": "DIGEST"
        }
      }
    },
    {
      "Sid": "DenyEveryOtherKey",
      "Effect": "Deny",
      "Action": "kms:*",
      "NotResource": [
        "arn:aws:kms:<AWS_REGION>:<AWS_ACCOUNT_ID>:key/<KEY_ID_1>",
        "arn:aws:kms:<AWS_REGION>:<AWS_ACCOUNT_ID>:key/<KEY_ID_2>"
      ]
    },
    {
      "Effect": "Allow",
      "Action": "sts:GetCallerIdentity",
      "Resource": "*"
    }
  ]
}
```

Capture the AssumeRole result without emitting its credential fields. Install
only the selected temporary profile in an owned directory with mode `0700`;
`credentials` and `config` must be regular files with mode `0600`. Never paste
the response into chat or a shell command, or commit the files. Server delivery
requires a protected installer over SSH/stdin and a dedicated signer identity;
the local operator command is not a public server deployment.

Verify the exact assumed-role ARN before any KMS call. Then validate public keys
and require `DryRunOperationException` from both signing DryRuns, and access
denial for excluded keys. DryRun creates no signature, as described in the
[AWS permission-testing documentation](https://docs.aws.amazon.com/kms/latest/developerguide/testing-permissions.html).

Credential expiry and key migration are separate. Record session expiry and
renew through the same operator bootstrap before the next demonstration; no
unattended renewal is installed. KMS DIGEST restrictions do not enforce a chain,
recipient or budget: the scoped signer and budget contract still enforce them.
