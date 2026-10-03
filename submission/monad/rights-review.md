# Rights review — license choice pending

This document is an internal checklist, not a legal opinion or a third-party notice bundle. Permission is recorded for the user-authorized first-party code in this package, with private keys and credentials excluded. The team is Agentonomy and the working contact is `fengjie@alvinsclub.ai`. The remaining first-party packaging decision is whether to apply one uniform OSI license; that choice must not be implied for dependencies or brand assets.

## Current recommendation

Choose one uniform OSI license for the eligible first-party source only after the final clean package is selected. Preserve every third-party notice and each dependency's own license terms. Until that choice is made, do not present the repository as uniformly OSI licensed.

The exported source records its provenance in [`docs/source-manifest.json`](../../docs/source-manifest.json), and the export README says that no additional repository-wide open-source license has been granted. Some new Solidity files carry `SPDX-License-Identifier: UNLICENSED`; this draft does not change those headers. The standalone source tree has no runtime dependency on an outside Clink checkout.

## Material inventory

| Material | Current handling | Owner action before submission |
| --- | --- | --- |
| First-party Core, Marketplace, Node, facilitator and composition code | Exported from the Clink working tree with selected new implementation files; user authorization covers the included code, excluding private keys and credentials | Bind the final package to a clean commit; choose the uniform OSI license for eligible first-party files only |
| New budget executor, protocol, backend, tests and docs | Local worktree draft on `codex/monad-budget-commerce`; included files are within the same user-authorized code scope | Bind the final package to a clean commit and preserve existing SPDX/provenance notices |
| Python packages, Foundry, Solidity compiler and other dependencies | Used under their own package/repository terms; installed Python metadata is recorded in [`dependency-licenses.json`](dependency-licenses.json) | Preserve each dependency's license and notice files; this inventory is not a license grant |
| Existing in-file notices | Retained where present | Do not remove, rewrite, or imply a broader grant |
| CSV examples and test fixtures | Synthetic, repository-local examples | Confirm they contain no personal, customer, or production financial data |
| Wallet keys and RPC credentials | Not intended for source or package; local test keys are generated in memory | Keep them excluded from source, logs, artifacts and screenshots |
| Logo and brand assets | Existing tracked source copy: `apps/node/clink_node/static/agentonomy-mark.png`, PNG 512 × 512 pixels, SHA-256 `078b83d5159b34614a372fb0ddc654ccad5942666241f7b25e92fea3ba501d2d`; provenance is also in `docs/source-manifest.json` | No new logo was created; this record makes no license or publication claim for the asset |
| Demo/service claims | Local HTTP merchant and Anvil only | Do not turn local behavior into a public deployment, audit, or adoption claim |

## Package fields

These fields record the known scope. A clean commit and the uniform OSI license choice are packaging steps, not requests to re-confirm the already recorded code authorization or team contact:

```text
rights_confirmed: true (user-authorized first-party code; private keys and credentials excluded)
submitter: Agentonomy
contact: fengjie@alvinsclub.ai
submission_date: PENDING_CLEAN_PACKAGE
source_commit: PENDING_CLEAN_COMMIT
first_party_rights_confirmed: true (scope above)
dependency_inventory_generated: true
branding_permission: NOT_CLAIMED (source provenance recorded)
license_decision: PENDING_UNIFORM_OSI_CHOICE
```

The authorization statement applies to the user-authorized first-party source and synthetic local fixtures in this package. It excludes private keys, credentials and third-party components. Dependency notices remain governed by their own terms, and the existing logo is recorded for provenance without a license claim.

## AI-assisted development disclosure

The implementation used AI coding assistance in the working process. The final submission should disclose that assistance in the form requested by the event and should distinguish imported Clink code, new first-party work, and generated/modified material. Do not describe the entire tree as having one creation date merely because the submission package is exported on one date.

## What is not claimed

This draft makes no claim of a uniform OSI license, third-party license grant, logo license, security audit, production deployment, official USDC status, customer adoption or revenue. Keep the package blocked only on the remaining license choice, clean-commit binding, and the separate public deployment evidence.
