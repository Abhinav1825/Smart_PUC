/**
 * Smart PUC — MultiSigAdmin deployment helper
 * ============================================
 *
 * Deploys a 2-of-3 MultiSigAdmin and transfers the EmissionRegistry
 * admin role to it. Intended to be run AFTER scripts/deploy.js so the
 * registry proxy already exists. See docs/MULTISIG.md.
 *
 * Signer selection
 * ----------------
 * By default this script uses signers [1], [2], [3] from ethers.getSigners()
 * so the flow works out of the box on local Hardhat and Ganache where
 * the first 10 test accounts are always present. For Amoy / mainnet,
 * override via the MULTISIG_SIGNERS environment variable:
 *
 *     MULTISIG_SIGNERS="0xAAA...,0xBBB...,0xCCC..." \
 *         npx hardhat run scripts/deploy_multisig.js --network amoy
 *
 * The threshold defaults to 2 and can be overridden with MULTISIG_THRESHOLD.
 *
 * Zero-cost on Hardhat / Amoy. On mainnet the deployment cost is
 * a single MultiSigAdmin create (~1 million gas) plus one
 * transferAdmin() call (~30k gas).
 */

const hre = require("hardhat");
const { ethers } = hre;
const path = require("path");
const fs = require("fs");

function readDeployedRegistryAddress() {
  // Prefer the Truffle-shape artifact written by scripts/deploy.js.
  const artifactPath = path.join(__dirname, "..", "build", "contracts", "EmissionRegistry.json");
  if (!fs.existsSync(artifactPath)) {
    throw new Error(
      "build/contracts/EmissionRegistry.json not found. " +
      "Run scripts/deploy.js before scripts/deploy_multisig.js."
    );
  }
  const art = JSON.parse(fs.readFileSync(artifactPath, "utf8"));
  const networks = art.networks || {};
  const ids = Object.keys(networks);
  if (ids.length === 0) {
    throw new Error("EmissionRegistry.json has no network entries.");
  }
  // Use the most recent network entry — deploy.js overwrites by chainId.
  const last = ids[ids.length - 1];
  return networks[last].address;
}

async function main() {
  console.log("\n=============================================");
  console.log("  Smart PUC — MultiSigAdmin Deploy & Handoff");
  console.log("=============================================\n");

  const signers = await ethers.getSigners();
  const admin = signers[0];

  // Select the signers for the multisig.
  let signerAddrs;
  if (process.env.MULTISIG_SIGNERS) {
    signerAddrs = process.env.MULTISIG_SIGNERS.split(",").map((a) => a.trim()).filter(Boolean);
  } else {
    signerAddrs = signers.slice(1, 4).map((s) => s.address);
    if (signerAddrs.length < 3) {
      // Testnet with only one funded account: fall back to admin×3 —
      // this is NOT secure but lets the flow run for a demo / dry-run.
      signerAddrs = [admin.address, admin.address, admin.address];
      console.warn(
        "[warn] Network only has one funded signer; falling back to admin×3. " +
        "This is for DEMO ONLY — set MULTISIG_SIGNERS in a real deployment."
      );
    }
  }
  const threshold = Number(process.env.MULTISIG_THRESHOLD || 2);

  console.log(`[info] Deployer   : ${admin.address}`);
  console.log(`[info] Signers    : ${signerAddrs.join(", ")}`);
  console.log(`[info] Threshold  : ${threshold}-of-${signerAddrs.length}`);

  // Guard against the duplicate-signer check in the constructor.
  const unique = new Set(signerAddrs.map((a) => a.toLowerCase()));
  if (unique.size !== signerAddrs.length) {
    throw new Error(
      "Duplicate signer addresses detected. MultiSigAdmin requires unique signers. " +
      "Either fund more accounts or set MULTISIG_SIGNERS explicitly."
    );
  }

  const registryAddress = readDeployedRegistryAddress();
  console.log(`[info] Registry   : ${registryAddress}\n`);

  const EmissionRegistry = await ethers.getContractFactory("EmissionRegistry", admin);
  const registry = EmissionRegistry.attach(registryAddress);

  console.log("[step] Deploying MultiSigAdmin ...");
  const MS = await ethers.getContractFactory("MultiSigAdmin", admin);
  const multisig = await MS.deploy(signerAddrs, threshold);
  await multisig.waitForDeployment();
  const multisigAddr = await multisig.getAddress();
  console.log(`[ok]   MultiSigAdmin deployed at ${multisigAddr}\n`);

  console.log("[step] Transferring EmissionRegistry admin to the multisig ...");
  const tx = await registry.transferAdmin(multisigAddr);
  await tx.wait();
  const newAdmin = await registry.admin();
  if (newAdmin.toLowerCase() !== multisigAddr.toLowerCase()) {
    throw new Error("Admin transfer failed — on-chain admin did not update.");
  }
  console.log(`[ok]   EmissionRegistry.admin is now ${newAdmin}\n`);

  // Append to the deployment record.
  try {
    const outFile = path.join(__dirname, "..", "docs", "DEPLOYED_ADDRESSES.json");
    let existing = {};
    if (fs.existsSync(outFile)) {
      try { existing = JSON.parse(fs.readFileSync(outFile, "utf8")); } catch (_) {}
    }
    const chainId = String((await ethers.provider.getNetwork()).chainId);
    if (!existing[chainId]) existing[chainId] = { contracts: {} };
    existing[chainId].contracts.MultiSigAdmin = multisigAddr;
    existing[chainId].multisig = {
      address: multisigAddr,
      signers: signerAddrs,
      threshold,
      attachedAt: new Date().toISOString(),
    };
    fs.writeFileSync(outFile, JSON.stringify(existing, null, 2) + "\n");
    console.log("[ok]   Deployment record updated");
  } catch (err) {
    console.warn("[warn] Could not update DEPLOYED_ADDRESSES.json:", err.message);
  }

  console.log("\n=============================================");
  console.log("  MultiSigAdmin handoff complete");
  console.log("=============================================");
  console.log(`  MultiSigAdmin : ${multisigAddr}`);
  console.log(`  Signers       : ${signerAddrs.join(", ")}`);
  console.log(`  Threshold     : ${threshold}`);
  console.log(`  Next step     : propose() via any signer, then confirm() + execute()`);
  console.log("  See docs/MULTISIG.md for the full flow.\n");
}

main()
  .then(() => process.exit(0))
  .catch((err) => {
    console.error(err);
    process.exit(1);
  });
