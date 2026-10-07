# Rights review — publication authorized, uniform license pending

This document is an internal checklist, not a legal opinion or a third-party notice bundle. Permission is recorded for the user-authorized first-party code in this package, with private keys and credentials excluded. The team is Agentonomy and the working contact is `fengjie@alvinsclub.ai`. The current authenticated form checkpoint has no license field. The selected-track deliverables were also rechecked and do not state a uniform OSI condition. Earlier guidance is not yet confirmed as a current event-wide requirement; check general terms/final declarations separately. This file does not grant or imply an OSI license.

## Current recommendation

Do not add or claim a uniform OSI license based on this draft. If the owner
chooses a license after the final clean package is selected, apply it only to
eligible first-party source and preserve every third-party notice and each
dependency's own license terms. Recheck the earlier event guidance before
making that choice; the current form checkpoint does not itself request a
license field.

The exported source records its provenance in [`docs/source-manifest.json`](../../docs/source-manifest.json), and the export README says that no additional repository-wide open-source license has been granted. Some new Solidity files carry `SPDX-License-Identifier: UNLICENSED`; this draft does not change those headers. The standalone source tree has no runtime dependency on an outside Clink checkout.

## Material inventory

| Material | Current handling | Owner action before submission |
| --- | --- | --- |
| First-party Core, Marketplace, Node, facilitator and composition code | Exported from the Clink working tree with selected new implementation files; user authorization covers the included code, excluding private keys and credentials | Bind the final package to a clean commit; make any later license choice only for eligible first-party files |
| New budget executor, protocol, backend, tests and docs | Included files are within the same user-authorized code scope | Bind the final package to a clean commit and preserve existing SPDX/provenance notices; do not add a license header by implication |
| Python packages, Foundry, Solidity compiler and other dependencies | Used under their own package/repository terms; installed Python metadata is recorded in [`dependency-licenses.json`](dependency-licenses.json) | Preserve each dependency's license and notice files; this inventory is not a license grant |
| Existing in-file notices | Retained where present | Do not remove, rewrite, or imply a broader grant |
| CSV examples and test fixtures | Synthetic, repository-local examples | Confirm they contain no personal, customer, or production financial data |
| Wallet keys and RPC credentials | Not intended for source or package; local test keys are generated in memory | Keep them excluded from source, logs, artifacts and screenshots |
| Logo and brand assets | Existing tracked source copy: `apps/node/clink_node/static/agentonomy-mark.png`, PNG 512 × 512 pixels, SHA-256 `078b83d5159b34614a372fb0ddc654ccad5942666241f7b25e92fea3ba501d2d`; provenance is also in `docs/source-manifest.json` | Existing logo is uploaded in the saved form; this record grants no separate open-source license for the asset |
| Demo/service claims | Monad testnet payment evidence with a loopback HTTP merchant; public review remains simulated | Do not turn loopback behavior or the simulated review site into a public service deployment, audit, or adoption claim |

## Package fields

These fields record the known scope. A clean commit and final rights review are
packaging steps, not requests to re-confirm the already recorded code
authorization or team contact:

```text
rights_confirmed: true (user-authorized first-party code; private keys and credentials excluded)
submitter: Agentonomy
contact: fengjie@alvinsclub.ai
submission_date: PENDING_CLEAN_PACKAGE
source_commit: PENDING_CLEAN_COMMIT
first_party_rights_confirmed: true (scope above)
dependency_inventory_generated: true
branding_use: EXISTING_LOGO_UPLOADED (source provenance recorded; no new asset license granted)
license_decision: PENDING_FINAL_REVIEW (no OSI license granted)
```

The authorization statement applies to the user-authorized first-party source and synthetic local fixtures in this package. It excludes private keys, credentials and third-party components. Dependency notices remain governed by their own terms, and the existing logo is recorded for provenance without granting an asset license.

## AI-assisted development disclosure

The implementation used AI coding assistance in the working process. The final submission should disclose that assistance in the form requested by the event and should distinguish imported Clink code, new first-party work, and generated/modified material. Do not describe the entire tree as having one creation date merely because the submission package is exported on one date.

## What is not claimed

This draft makes no claim of a uniform OSI license, third-party license grant,
logo license, security audit, production readiness, official USDC status,
customer adoption or revenue. The Monad payment evidence is real, but the
merchant UI/API remains loopback and the public review service is simulated.
Keep final packaging pending on clean-commit binding, rights review and the
remaining submission evidence; no license is granted by this document.
