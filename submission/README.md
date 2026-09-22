# Submission preparation

This directory is a blocked local draft for Agentonomy Commerce. It is not an
official pull request, deployment, rights attestation, or eligibility decision.

Build a reproducible draft from the committed source with:

```sh
.venv/bin/python scripts/package_submission.py \
  --output .artifacts/submissions/mcp-hackathon/hechooo-agentonomy-commerce
```

The package exports the exact Git `HEAD` tree into `source/`, writes a sorted
SHA-256 manifest, and records missing owner evidence in `preflight.json`.
Ignored runtime state, `.artifacts/`, generated submission directories, and
virtual environments are excluded; symlinks and unsupported source entries are
rejected rather than copied.
The default result must remain `status: blocked` until the owner supplies and
verifies the required inputs. A package is not an official submission.

The official pull request is expected to contain this prepared content under:

```text
submissions/mcp-hackathon/hechooo-agentonomy-commerce/
```

Copy or adapt the generated draft only after reviewing the official templates
and replacing every pending field with evidence. Keep the exported `source/`
tree bound to the exact clean commit named by `submission.json`; do not copy
working-tree or ignored files into it.

References used by this draft are recorded in `OFFICIAL_RULES.md`. They include
the [official submissions templates](https://github.com/xagentAI/xagt-plugin/tree/239140cc04dc82a121e9c31199bc8a12b510e130/submissions)
and the [reference validator](https://github.com/xagentAI/xagt-plugin/blob/239140cc04dc82a121e9c31199bc8a12b510e130/scripts/validate-submission.mjs).
The local package records a caller-provided validator path with
`--validator-path`; it does not depend on a machine-specific validator path.

Before an official submission, the owner must provide:

- legal submitter identity and support contact;
- fixed source repository and the clean deployed review commit;
- public HTTPS root API origin, health URL, proof response, and approved
  short-lived review access;
- rights to submit, reproduce, audit, test, archive, and publish the source,
  service, data, branding, and dependencies, with notices and restrictions;
- confirmation that the review window or any late-submission process permits
  this entry.

The local review service uses real loopback HTTP for the merchant and simulated
settlement. That behavior is evidence for the local contract only; it does not
prove a public deployment or a live blockchain transaction.
