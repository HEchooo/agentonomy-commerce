# Monad independent role session and public acceptance

Continue the completed local/KMS implementation at `2d34833`. The user authorized
the dedicated test role and bounded Monad testnet closed loop. Public acceptance
still requires actual deployment, external OKX authorizations and payment proof.

## Boundaries

- Standalone Commerce only. Do not change Clink DEV/PROD services or IAM policies.
- Bootstrap an independent temporary role session outside the ordinary runtime;
  the signer must not load the operator profile or long-term access keys.
- Session permission is limited to the existing execution and Gas test keys.
  Hosted response authentication and PROD keys are excluded.
- Chain 10143; buyer `0x59899831691aa79507818961773497c751bffc8b`.
- Exact two-contract deployment; fixed supply of 1.00 no-value TestUSD, a 1.00
  budget, 0.50 per-payment cap, 0.30 service price and one-day grant.
- Purchase signing scope cannot deploy contracts. A separate operator command
  verifies a concrete plan, saves signed attempts before broadcast and reconciles
  the same transaction after uncertain outcomes.
- Node, Marketplace, Core and Watcher do not load AWS credentials. The operator
  wallet keeps its private key and supplies its own signatures and finite approve.
- Local operator UI stays loopback-only. Public review site remains simulated
  until a separately validated public deployment exists.

## Remaining work

1. Verify a fresh assumed-role identity, both public addresses and two successful
   DryRuns; require response/PROD key access denial. Completed live on October 7;
   private evidence is kept outside Git. No signature or transaction was generated.
2. Enforce isolated temporary credentials, exact role identity and startup DryRun
   in the signing worker; completed, tested and independently approved in
   commit `49e60b8`.
3. Add and test the separate bounded deployment operator, durable recovery and
   dual-RPC Verified receipt validation. Implementation and local tests are
   complete; final independent specification and quality review approved the
   fixes, including a seven-test targeted recheck.
4. Fund the relayer with test MON, refresh the exact plan and deploy. Buyer had
   40 MON and relayer 0 MON on both RPCs at the October 7 check. Funding pending.
5. Start the operator interface, obtain buyer OKX signatures/finite approval,
   execute purchase and HTTP delivery, verify replay/restart/revocation.
6. Record public transaction/code hashes and actual acceptance results; prepare
   the recording sequence and update submission materials truthfully.

The October 7 role session expires on October 8 at 02:32 China time. This is
temporary access renewal, not a key migration, and not unattended renewal.
