/**
 * Smart PUC — Contract Deployment Script (Hardhat + ethers v6)
 * ============================================================
 *
 * Replaces migrations/1_initial_migration.js. Deploys the three core
 * contracts as UUPS upgradeable proxies (OpenZeppelin upgrades plugin),
 * wires them together with the correct roles, and writes the deployed
 * proxy addresses into the Truffle-compat `build/contracts/<Name>.json`
 * files so the Python backend keeps working unchanged.
 *
 * Deployment order (dependency chain):
 *   1. GreenToken         (no deps)          — deployProxy
 *   2. EmissionRegistry   (no deps)          — deployProxy
 *   3. PUCCertificate     (registry, token)  — deployProxy
 *
 * Post-deployment wiring:
 *   - GreenToken.setMinter(PUCCertificate, true)
 *   - EmissionRegistry.setPUCCertificateContract(PUCCertificate)
 *   - EmissionRegistry.setTestingStation(testingStation, true)
 *   - EmissionRegistry.setRegisteredDevice(obdDevice, true)
 *   - EmissionRegistry.setVehicleOwner("MH12AB1234", vehicleOwner)
 *   - PUCCertificate.setAuthorizedIssuer(testingStation, true)
 *   - PUCCertificate.setBaseURI("ipfs://")
 *
 * Account roles (matches Ganache --deterministic):
 *   accounts[0] = Admin (deploys + manages)
 *   accounts[1] = Testing Station
 *   accounts[2] = OBD Device
 *   accounts[3] = Vehicle Owner
 *
 * Usage:
 *     npx hardhat run scripts/deploy.js --network localhost
 *     npx hardhat run scripts/deploy.js --network amoy
 *     npx hardhat run scripts/deploy.js --network polygon
 *
 * Optional: deploy a 2-of-3 MultiSigAdmin and hand over admin rights to it
 * in the same deployment. Because `npx hardhat run` does not forward custom
 * CLI flags to the script, we gate this behaviour on an environment
 * variable so it works identically on Linux, macOS, and Windows:
 *
 *     # Linux / macOS:
 *     USE_MULTISIG=1 npx hardhat run scripts/deploy.js --network localhost
 *
 *     # Windows (cmd.exe):
 *     set USE_MULTISIG=1 && npx hardhat run scripts/deploy.js --network localhost
 *
 *     # Windows (PowerShell):
 *     $env:USE_MULTISIG=1; npx hardhat run scripts/deploy.js --network localhost
 *
 * When USE_MULTISIG=1, the script additionally:
 *   - Deploys MultiSigAdmin([deployer, signer1, signer2], threshold=2)
 *   - Transfers admin on EmissionRegistry + GreenToken to the multisig
 *   - Transfers authority on PUCCertificate to the multisig
 *   - Records the multisig address under the current chainId in
 *     docs/DEPLOYED_ADDRESSES.json (additive; existing entries preserved).
 *
 * If USE_MULTISIG is unset, "0", or empty, the legacy behaviour is
 * preserved byte-for-byte.
 */

const hre = require("hardhat");
const { ethers, upgrades } = hre;
const { flatten, recordAddress } = require("./flatten_artifacts");

async function main() {
  // Ensure flat artifact files exist so recordAddress() has a target.
  flatten();

  const signers = await ethers.getSigners();
  const [admin, testingStation, obdDevice, vehicleOwner] = signers;

  // When targeting remote networks we typically only have one signer (the
  // HD wallet). In that case the admin plays every role; this mirrors the
  // behaviour of `truffle migrate` on a real testnet.
  const station = testingStation || admin;
  const device = obdDevice || admin;
  const owner = vehicleOwner || admin;

  const chainId = Number((await ethers.provider.getNetwork()).chainId);

  console.log("\n========================================");
  console.log("Smart PUC — 3-Node Contract Deployment");
  console.log("========================================");
  console.log(`  Network         : ${hre.network.name} (chainId ${chainId})`);
  console.log(`  Admin           : ${admin.address}`);
  console.log(`  Testing Station : ${station.address}`);
  console.log(`  OBD Device      : ${device.address}`);
  console.log(`  Vehicle Owner   : ${owner.address}`);
  console.log("----------------------------------------\n");

  // ── Step 1: GreenToken (UUPS proxy) ────────────────────────────────
  console.log("[1/3] Deploying GreenToken (ERC-20, UUPS proxy)...");
  const GreenToken = await ethers.getContractFactory("GreenToken", admin);
  const greenToken = await upgrades.deployProxy(GreenToken, [], {
    kind: "uups",
    initializer: "initialize",
  });
  await greenToken.waitForDeployment();
  const greenTokenAddress = await greenToken.getAddress();
  console.log(`  GreenToken proxy : ${greenTokenAddress}\n`);

  // ── Step 2: EmissionRegistry (UUPS proxy) ──────────────────────────
  console.log("[2/3] Deploying EmissionRegistry (UUPS proxy)...");
  const EmissionRegistry = await ethers.getContractFactory("EmissionRegistry", admin);
  const registry = await upgrades.deployProxy(EmissionRegistry, [], {
    kind: "uups",
    initializer: "initialize",
  });
  await registry.waitForDeployment();
  const registryAddress = await registry.getAddress();
  console.log(`  EmissionRegistry proxy : ${registryAddress}\n`);

  // ── Step 3: PUCCertificate (UUPS proxy) ────────────────────────────
  console.log("[3/3] Deploying PUCCertificate (ERC-721 NFT, UUPS proxy)...");
  const PUCCertificate = await ethers.getContractFactory("PUCCertificate", admin);
  const puc = await upgrades.deployProxy(
    PUCCertificate,
    [registryAddress, greenTokenAddress],
    { kind: "uups", initializer: "initialize" }
  );
  await puc.waitForDeployment();
  const pucAddress = await puc.getAddress();
  console.log(`  PUCCertificate proxy : ${pucAddress}\n`);

  // ── Step 4: Wire everything up ─────────────────────────────────────
  console.log("Wiring contracts...");

  let tx;
  tx = await greenToken.setMinter(pucAddress, true);
  await tx.wait();
  console.log("  GreenToken -> PUCCertificate authorized as minter");

  tx = await registry.setPUCCertificateContract(pucAddress);
  await tx.wait();
  console.log("  EmissionRegistry -> PUCCertificate linked");

  if (station.address !== admin.address) {
    tx = await registry.setTestingStation(station.address, true);
    await tx.wait();
    console.log(`  EmissionRegistry -> Testing Station authorized (${station.address})`);
  }

  if (device.address !== admin.address) {
    tx = await registry.setRegisteredDevice(device.address, true);
    await tx.wait();
    console.log(`  EmissionRegistry -> OBD Device registered (${device.address})`);
  }

  tx = await registry.setVehicleOwner("MH12AB1234", owner.address);
  await tx.wait();
  console.log(`  EmissionRegistry -> Vehicle Owner registered for MH12AB1234 (${owner.address})`);

  if (station.address !== admin.address) {
    tx = await puc.setAuthorizedIssuer(station.address, true);
    await tx.wait();
    console.log("  PUCCertificate -> Testing Station authorized as issuer");
  }

  tx = await puc.setBaseURI("ipfs://");
  await tx.wait();
  console.log('  PUCCertificate -> Base URI set to "ipfs://"');

  // Tag the demo vehicle as BS-VI (default, but kept explicit so reviewers
  // can see the code path is exercised at deploy time).
  tx = await registry.setVehicleStandard("MH12AB1234", 0); // 0 = BS6
  await tx.wait();
  console.log("  EmissionRegistry -> Vehicle MH12AB1234 tagged as BS-VI");

  // ── Step 5: Write Truffle-shape artifacts ──────────────────────────
  recordAddress("GreenToken", chainId, greenTokenAddress);
  recordAddress("EmissionRegistry", chainId, registryAddress);
  recordAddress("PUCCertificate", chainId, pucAddress);

  // Persist a machine-readable deployment record for the paper's
  // "data availability" section and for the OBD device to read its
  // EIP-712 domain parameters from.
  try {
    const fs = require("fs");
    const path = require("path");
    const outDir = path.join(__dirname, "..", "docs");
    if (!fs.existsSync(outDir)) fs.mkdirSync(outDir, { recursive: true });
    const outFile = path.join(outDir, "DEPLOYED_ADDRESSES.json");
    let existing = {};
    if (fs.existsSync(outFile)) {
      try { existing = JSON.parse(fs.readFileSync(outFile, "utf8")); } catch (_) {}
    }
    existing[String(chainId)] = {
      network: hre.network.name,
      chainId,
      deployedAt: new Date().toISOString(),
      contracts: {
        GreenToken: greenTokenAddress,
        EmissionRegistry: registryAddress,
        PUCCertificate: pucAddress,
      },
      eip712Domain: {
        name: "SmartPUC",
        version: "3.2",
        chainId,
        verifyingContract: registryAddress,
      },
    };
    fs.writeFileSync(outFile, JSON.stringify(existing, null, 2) + "\n");
    console.log(`  Deployed addresses saved to docs/DEPLOYED_ADDRESSES.json`);
  } catch (err) {
    console.warn("  Could not write DEPLOYED_ADDRESSES.json:", err.message);
  }

  // ── Step 5b: Auto-sync .env with new REGISTRY_ADDRESS ──────────────
  try {
    const fs = require("fs");
    const path = require("path");
    const envPath = path.join(__dirname, "..", ".env");
    if (fs.existsSync(envPath)) {
      let envContent = fs.readFileSync(envPath, "utf8");
      // Replace existing REGISTRY_ADDRESS line (whether empty or populated)
      if (/^REGISTRY_ADDRESS=.*$/m.test(envContent)) {
        envContent = envContent.replace(
          /^REGISTRY_ADDRESS=.*$/m,
          `REGISTRY_ADDRESS=${registryAddress}`
        );
      } else {
        // If the key doesn't exist at all, append it
        envContent = envContent.trimEnd() + `\nREGISTRY_ADDRESS=${registryAddress}\n`;
      }
      fs.writeFileSync(envPath, envContent);
      console.log("  \u2713 .env updated with new contract addresses");
    } else {
      console.log("  .env file not found — skipping auto-sync (copy .env.example to .env)");
    }
  } catch (err) {
    console.warn("  Could not update .env:", err.message);
  }

  // ── Optional Step 6: MultiSigAdmin handoff (USE_MULTISIG=1) ────────
  const useMultisig = ["1", "true", "yes", "on"].includes(
    String(process.env.USE_MULTISIG || "").toLowerCase()
  );
  let multisigAddress = null;
  if (useMultisig) {
    console.log("\n[USE_MULTISIG=1] Deploying MultiSigAdmin and handing over admin roles...");

    // Pick 3 distinct signers. On mainnet / amoy where only one account is
    // funded, fall back to the dedicated deploy_multisig.js script instead.
    if (signers.length < 3) {
      throw new Error(
        "USE_MULTISIG=1 requires at least 3 signers in the network account set. " +
        "On single-account networks, run scripts/deploy_multisig.js separately."
      );
    }
    const [, signer1, signer2] = signers;
    const msSignerSet = [admin.address, signer1.address, signer2.address];
    const threshold = 2;

    const MultiSigAdmin = await ethers.getContractFactory("MultiSigAdmin", admin);
    const multisig = await MultiSigAdmin.deploy(msSignerSet, threshold);
    await multisig.waitForDeployment();
    multisigAddress = await multisig.getAddress();
    console.log(`  MultiSigAdmin deployed  : ${multisigAddress}`);
    console.log(`  Signers                 : ${msSignerSet.join(", ")}`);
    console.log(`  Threshold               : ${threshold}-of-${msSignerSet.length}`);

    const txER = await registry.transferAdmin(multisigAddress);
    await txER.wait();
    console.log(`  EmissionRegistry.transferAdmin  tx: ${txER.hash}`);

    const txGT = await greenToken.transferAdmin(multisigAddress);
    await txGT.wait();
    console.log(`  GreenToken.transferAdmin        tx: ${txGT.hash}`);

    const txPUC = await puc.transferAuthority(multisigAddress);
    await txPUC.wait();
    console.log(`  PUCCertificate.transferAuthority tx: ${txPUC.hash}`);

    // Additively record the multisig in DEPLOYED_ADDRESSES.json without
    // clobbering anything already written for this chainId.
    try {
      const fs = require("fs");
      const path = require("path");
      const outFile = path.join(__dirname, "..", "docs", "DEPLOYED_ADDRESSES.json");
      let existing = {};
      if (fs.existsSync(outFile)) {
        try { existing = JSON.parse(fs.readFileSync(outFile, "utf8")); } catch (_) {}
      }
      const key = String(chainId);
      if (!existing[key]) existing[key] = { chainId, contracts: {} };
      if (!existing[key].contracts) existing[key].contracts = {};
      existing[key].contracts.MultiSigAdmin = multisigAddress;
      existing[key].multisig = {
        address: multisigAddress,
        signers: msSignerSet,
        threshold,
        attachedAt: new Date().toISOString(),
      };
      fs.writeFileSync(outFile, JSON.stringify(existing, null, 2) + "\n");
      console.log("  MultiSigAdmin saved to docs/DEPLOYED_ADDRESSES.json");
    } catch (err) {
      console.warn("  Could not update DEPLOYED_ADDRESSES.json with multisig:", err.message);
    }
  }

  console.log("\n========================================");
  console.log("Deployment Complete!");
  console.log("========================================");
  console.log(`  GreenToken       : ${greenTokenAddress}`);
  console.log(`  EmissionRegistry : ${registryAddress}`);
  console.log(`  PUCCertificate   : ${pucAddress}`);
  if (multisigAddress) {
    console.log(`  MultiSigAdmin    : ${multisigAddress}`);
  }
  console.log("========================================\n");
}

main()
  .then(() => process.exit(0))
  .catch((err) => {
    console.error(err);
    process.exit(1);
  });
