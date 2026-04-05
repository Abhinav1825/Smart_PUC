# Smart PUC — MultiSigAdmin governance (v3.2.2)

Closes audit item **S5** ("single-EOA admin is a single point of
failure"). A compromised admin key could previously pause the registry,
re-assign every vehicle owner, transfer the admin role to an attacker,
or authorize an arbitrary UUPS upgrade. The `MultiSigAdmin` contract
raises the attacker cost to `threshold` compromised signer keys.

## Deployment path (local / Amoy)

The MultiSig is **opt-in**: a fresh deployment of Smart PUC still starts
with the deployer EOA as admin, exactly as before. To adopt multisig
governance, run the following **after** the main deploy script:

```bash
# 1) Deploy a 2-of-3 multisig. Replace the signer addresses with the real
#    ones from the participating institutions (e.g. regulator, station
#    operator, audit firm).
npx hardhat run scripts/deploy_multisig.js --network amoy

# 2) Transfer admin on each owned contract. The deploy_multisig script
#    does this automatically for EmissionRegistry; GreenToken and
#    PUCCertificate use OpenZeppelin AccessControl and are left alone.
```

See `scripts/deploy_multisig.js` for the reference script. The script is
**free to run on local Hardhat and Amoy testnet** (no mainnet gas
cost) so the paper's reproducibility section can include a live demo of
the governance flow.

## Operational flow

```text
    Signer A                Signer B                Signer C
       │                        │                        │
       │  propose(target, data) │                        │
       ├───────────────────────►│                        │
       │  (proposer auto-confirms, count = 1)            │
       │                        │                        │
       │                        │  confirm(id)           │
       │                        ├───────────────────────►│
       │                        │  (count = 2, ≥ threshold)
       │                        │                        │
       │                        │                        │  execute(id)
       │                        │                        ├──► target.call(data)
       │                        │                        │
       ▼                        ▼                        ▼
```

## Why not Gnosis Safe?

We deliberately avoid the Gnosis Safe codebase for the research
prototype because:

1. **Code size.** A Safe deployment pulls in ~40 Solidity files and
   costs roughly 1.6 million gas to initialise. `MultiSigAdmin.sol` is
   ~180 lines and compiles to a single runtime artifact.
2. **Review burden.** For a journal submission, asking reviewers to
   take a 40-file external system on trust is harder than asking them
   to read a self-contained ~180 line contract.
3. **Feature surface.** Smart PUC's admin only needs `call(target, data)`
   with N-of-M confirmations. Safe's module system, fallback handler,
   and nested guard contracts are irrelevant to this threat model.

A production deployment may choose Gnosis Safe for ecosystem tooling
reasons (Safe{Wallet} UI, hardware wallet integrations, DeFi composability).
The `transferAdmin` entry point is deliberately unopinionated so either
path is supported.

## Threat model (what multisig does and does not solve)

**Solved:**

- Single-key compromise of the admin EOA.
- An internal rogue actor attempting to unilaterally pause the
  registry or transfer admin rights.
- Accidental admin actions from a misconfigured script — the
  confirm-then-execute flow provides a review window.

**Not solved:**

- Collusion of `threshold` signers.
- Compromise of `threshold` distinct signer keys.
- Admin keys held by the same human / same hardware.
- Social engineering targeting enough signers simultaneously.

**Out of scope (future work):**

- Timelock on the execute step (see §V of the paper — v3.3 roadmap).
- On-chain voting / DAO governance (not a research contribution for
  the IoT-Journal submission; left to operational discretion).

## Tests

The governance path is covered by TC-65..TC-71 in
`test/SmartPUC.test.js`, exercising:

- Constructor invariants (zero/duplicate signers, threshold bounds)
- Non-signer proposal rejection
- Below-threshold execution rejection
- 2-of-3 end-to-end execution
- Re-execution prevention
- Confirmation revocation
- Post-transfer blocking of the old admin EOA
