/**
 * Smart PUC — Migration 1: Deploy Core Contracts
 *
 * Deployment order (dependency chain):
 *   1. GreenToken (no dependencies)
 *   2. EmissionRegistry (no dependencies)
 *   3. PUCCertificate (depends on EmissionRegistry + GreenToken)
 *
 * Post-deployment wiring:
 *   - GreenToken.setMinter(PUCCertificate.address)
 *   - EmissionRegistry.setPUCCertificateContract(PUCCertificate.address)
 *   - EmissionRegistry.setTestingStation(accounts[1])
 *   - EmissionRegistry.setRegisteredDevice(accounts[2])
 *   - EmissionRegistry.setVehicleOwner("MH12AB1234", accounts[3])
 *   - PUCCertificate.setBaseURI("ipfs://")
 *
 * Ganache Account Roles:
 *   accounts[0] = Admin (deploys, manages system)
 *   accounts[1] = Testing Station (submits emission records)
 *   accounts[2] = OBD Device (signs telemetry data)
 *   accounts[3] = Vehicle Owner (claims NFTs, receives GCT)
 */

const GreenToken = artifacts.require("GreenToken");
const EmissionRegistry = artifacts.require("EmissionRegistry");
const PUCCertificate = artifacts.require("PUCCertificate");

module.exports = async function (deployer, network, accounts) {
  const admin = accounts[0];
  const testingStation = accounts[1];
  const obdDevice = accounts[2];
  const vehicleOwner = accounts[3];

  console.log("\n========================================");
  console.log("Smart PUC — 3-Node Contract Deployment");
  console.log("========================================");
  console.log(`  Admin           : ${admin}`);
  console.log(`  Testing Station : ${testingStation}`);
  console.log(`  OBD Device      : ${obdDevice}`);
  console.log(`  Vehicle Owner   : ${vehicleOwner}`);
  console.log("----------------------------------------\n");

  // Step 1: Deploy GreenToken
  console.log("[1/3] Deploying GreenToken (ERC-20)...");
  await deployer.deploy(GreenToken, { from: admin });
  const greenToken = await GreenToken.deployed();
  console.log(`  GreenToken deployed at: ${greenToken.address}\n`);

  // Step 2: Deploy EmissionRegistry
  console.log("[2/3] Deploying EmissionRegistry...");
  await deployer.deploy(EmissionRegistry, { from: admin });
  const registry = await EmissionRegistry.deployed();
  console.log(`  EmissionRegistry deployed at: ${registry.address}\n`);

  // Step 3: Deploy PUCCertificate with contract references
  console.log("[3/3] Deploying PUCCertificate (ERC-721 NFT)...");
  await deployer.deploy(PUCCertificate, registry.address, greenToken.address, { from: admin });
  const pucCert = await PUCCertificate.deployed();
  console.log(`  PUCCertificate deployed at: ${pucCert.address}\n`);

  // Step 4: Wire contracts together
  console.log("Wiring contracts...");

  // GreenToken: authorize PUCCertificate as minter
  await greenToken.setMinter(pucCert.address, true, { from: admin });
  console.log("  GreenToken -> PUCCertificate authorized as minter");

  // EmissionRegistry: set PUCCertificate address
  await registry.setPUCCertificateContract(pucCert.address, { from: admin });
  console.log("  EmissionRegistry -> PUCCertificate linked");

  // EmissionRegistry: authorize testing station
  await registry.setTestingStation(testingStation, true, { from: admin });
  console.log(`  EmissionRegistry -> Testing Station authorized (${testingStation})`);

  // EmissionRegistry: register OBD device
  await registry.setRegisteredDevice(obdDevice, true, { from: admin });
  console.log(`  EmissionRegistry -> OBD Device registered (${obdDevice})`);

  // EmissionRegistry: register vehicle owner for default test vehicle
  await registry.setVehicleOwner("MH12AB1234", vehicleOwner, { from: admin });
  console.log(`  EmissionRegistry -> Vehicle Owner registered for MH12AB1234 (${vehicleOwner})`);

  // PUCCertificate: authorize testing station as issuer
  await pucCert.setAuthorizedIssuer(testingStation, true, { from: admin });
  console.log(`  PUCCertificate -> Testing Station authorized as issuer`);

  // Step 5: Set IPFS base URI for PUCCertificate metadata
  await pucCert.setBaseURI("ipfs://", { from: admin });
  console.log(`  PUCCertificate -> Base URI set to "ipfs://"`);

  console.log("\n========================================");
  console.log("Deployment Complete!");
  console.log("========================================");
  console.log(`  GreenToken       : ${greenToken.address}`);
  console.log(`  EmissionRegistry : ${registry.address}`);
  console.log(`  PUCCertificate   : ${pucCert.address}`);
  console.log("========================================\n");
};
