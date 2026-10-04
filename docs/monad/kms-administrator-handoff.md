# Monad KMS administrator handoff

This public document describes the handoff process without publishing cloud
account, key, or operator identifiers. The exact values and approved policy
are kept in the ignored local operator artifact
`.artifacts/monad-kms/administrator-handoff.md`; the root agent can use that
file when preparing the durable private handoff.

The current operator must use an already-authorized administrator identity for
the single policy application. Do not assume a development profile is
authorized. The key creation and signing status must be recorded in the local
artifact before the administrator acts. This document contains no credentials
and does not authorize a production change.

Using the exact values from the local artifact, save the following policy as
`kms-key-use-policy.json`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AgentonomyHackathonMonadKeyUse",
      "Effect": "Allow",
      "Action": [
        "kms:DescribeKey",
        "kms:GetPublicKey",
        "kms:Sign"
      ],
      "Resource": [
        "arn:aws:kms:<AWS_REGION>:<AWS_ACCOUNT_ID>:key/<KEY_ID_1>",
        "arn:aws:kms:<AWS_REGION>:<AWS_ACCOUNT_ID>:key/<KEY_ID_2>"
      ]
    }
  ]
}
```

Using that file and the exact operator, policy name, and region from the local
artifact, the administrator may apply the policy with this command:

```sh
aws iam put-user-policy \
  --user-name <IAM_USER> \
  --policy-name <UNIQUE_POLICY_NAME> \
  --policy-document file://kms-key-use-policy.json \
  --region <AWS_REGION>
```

The policy is restricted to `DescribeKey`, `GetPublicKey`, and `Sign` on the
two listed key ARNs. It contains no wildcard resource, no IAM administration,
no key-policy modification, no deletion permission, and no production access.
Temporary credential renewal is a separate prerequisite and is not solved by
this policy. After the administrator applies it, the signer owner must perform
the planned KMS self-checks before any later deployment work; this handoff does
not sign, broadcast, or claim a Monad deployment.
