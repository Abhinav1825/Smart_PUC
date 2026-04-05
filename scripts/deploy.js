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

  // ── Step 5: Write Truffle-shape artifacts ──────────────────────────
  recordAddress("GreenToken", chainId, greenTokenAddress);
  recordAddress("EmissionRegistry", chainId, registryAddress);
  recordAddress("PUCCertificate", chainId, pucAddress);

  console.log("\n========================================");
  console.log("Deployment Complete!");
  console.log("========================================");
  console.log(`  GreenToken       : ${greenTokenAddress}`);
  console.log(`  EmissionRegistry : ${registryAddress}`);
  console.log(`  PUCCertificate   : ${pucAddress}`);
  console.log("========================================\n");
}

main()
  .then(() => process.exit(0))
  .catch((err) => {
    console.error(err);
    process.exit(1);
  });
