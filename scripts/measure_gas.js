/**
 * Smart PUC — Gas Measurement Harness (Hardhat + ethers v6)
 * ==========================================================
 *
 * Measures gas usage of every state-changing operation in the three
 * core contracts (EmissionRegistry, PUCCertificate, GreenToken) and
 * writes the results to docs/gas_report.json plus a human-readable
 * Markdown table to stdout.
 *
 * Usage:
 *     npx hardhat run scripts/measure_gas.js --network localhost
 *     # or, using the in-process Hardhat network (no external node):
 *     npx hardhat run scripts/measure_gas.js
 *
 * The script deploys fresh UUPS proxies against the selected network, so
 * it does not require a prior `scripts/deploy.js` run. If you need the
 * numbers that correspond to a specific persisted deployment, run the
 * deploy script first and edit the `mode` block below.
 */

const fs = require("fs");
const path = require("path");
const hre = require("hardhat");
const { ethers, upgrades } = hre;

const POLYGON_GWEI = 50;
const POLYGON_MATIC_USD = 0.70;
const ETH_GWEI = 15;
const ETH_USD = 2400;

function gasToUsd(gas, gwei, tokenUsd) {
  const ethPerGas = Number(gwei) * 1e-9;
  const ethCost = Number(gas) * ethPerGas;
  return ethCost * tokenUsd;
}

function row(op, contract, gas) {
  const gasNum = Number(gas);
  const polygon = gasToUsd(gasNum, POLYGON_GWEI, POLYGON_MATIC_USD);
  const eth = gasToUsd(gasNum, ETH_GWEI, ETH_USD);
  return {
    operation: op,
    contract: contract,
    gas: gasNum,
    polygon_usd: Number(polygon.toFixed(6)),
    ethereum_usd: Number(eth.toFixed(4)),
  };
}

function nonceFromSeed(seed) {
  return ethers.keccak256(ethers.AbiCoder.defaultAbiCoder().encode(["uint256"], [seed]));
}

async function signEmission(signer, registry, vehicleId, co2, co, nox, hc, pm25, ts, nonce) {
  const net = await ethers.provider.getNetwork();
  const domain = {
    name: "SmartPUC",
    version: "3.2",
    chainId: net.chainId,
    verifyingContract: await registry.getAddress(),
  };
  const types = {
    EmissionReading: [
      { name: "vehicleId", type: "string" },
      { name: "co2", type: "uint256" },
      { name: "co", type: "uint256" },
      { name: "nox", type: "uint256" },
      { name: "hc", type: "uint256" },
      { name: "pm25", type: "uint256" },
      { name: "timestamp", type: "uint256" },
      { name: "nonce", type: "bytes32" },
    ],
  };
  return signer.signTypedData(
    domain,
    types,
    { vehicleId, co2, co, nox, hc, pm25, timestamp: ts, nonce }
  );
}

async function gasOf(txPromise) {
  const tx = await txPromise;
  const receipt = await tx.wait();
  return receipt.gasUsed;
}

async function main() {
  const [admin, station, device, owner] = await ethers.getSigners();
  const results = [];

  // ── Fresh deployment to measure each op in isolation ───────────────
  const GreenToken = await ethers.getContractFactory("GreenToken", admin);
  const greenToken = await upgrades.deployProxy(GreenToken, [], { kind: "uups", initializer: "initialize" });
  await greenToken.waitForDeployment();

  const EmissionRegistry = await ethers.getContractFactory("EmissionRegistry", admin);
  const registry = await upgrades.deployProxy(EmissionRegistry, [], { kind: "uups", initializer: "initialize" });
  await registry.waitForDeployment();

  const PUCCertificate = await ethers.getContractFactory("PUCCertificate", admin);
  const puc = await upgrades.deployProxy(
    PUCCertificate,
    [await registry.getAddress(), await greenToken.getAddress()],
    { kind: "uups", initializer: "initialize" }
  );
  await puc.waitForDeployment();

  await (await registry.setTestingStation(station.address, true)).wait();
  await (await registry.setRegisteredDevice(device.address, true)).wait();
  await (await registry.setPUCCertificateContract(await puc.getAddress())).wait();
  await (await greenToken.setMinter(await puc.getAddress(), true)).wait();
  await (await puc.setAuthorizedIssuer(station.address, true)).wait();

  const mkSig = async (vid, co2, co, nox, hc, pm25, ts, n) =>
    signEmission(device, registry, vid, co2, co, nox, hc, pm25, ts, n);

  // 1. storeEmission — first submission for a new vehicle
  {
    const vid = "BENCH01XX0001";
    const n = nonceFromSeed(1);
    const ts = BigInt(Math.floor(Date.now() / 1000));
    const sig = await mkSig(vid, 110000n, 800n, 50n, 80n, 4n, ts, n);
    const gas = await gasOf(registry.connect(station).storeEmission(
      vid, 110000n, 800n, 50n, 80n, 4n, 1000n, 5000n, 0, ts, n, sig
    ));
    results.push(row("storeEmission (first submission for new vehicle)", "EmissionRegistry", gas));
  }

  // 2. storeEmission — subsequent PASS (same vehicle)
  {
    const vid = "BENCH01XX0001";
    const n = nonceFromSeed(2);
    const ts = BigInt(Math.floor(Date.now() / 1000) + 100);
    const sig = await mkSig(vid, 110000n, 800n, 50n, 80n, 4n, ts, n);
    const gas = await gasOf(registry.connect(station).storeEmission(
      vid, 110000n, 800n, 50n, 80n, 4n, 1000n, 5000n, 0, ts, n, sig
    ));
    results.push(row("storeEmission (subsequent PASS)", "EmissionRegistry", gas));
  }

  // 3. storeEmission — FAIL + pollutant events
  {
    const vid = "BENCH01XX0001";
    const n = nonceFromSeed(3);
    const ts = BigInt(Math.floor(Date.now() / 1000) + 200);
    const sig = await mkSig(vid, 200000n, 2500n, 300n, 300n, 20n, ts, n);
    const gas = await gasOf(registry.connect(station).storeEmission(
      vid, 200000n, 2500n, 300n, 300n, 20n, 7000n, 8000n, 1, ts, n, sig
    ));
    results.push(row("storeEmission (FAIL + pollutant events)", "EmissionRegistry", gas));
  }

  // 4. Three consecutive PASS records for a second vehicle (prep for cert issuance)
  const certVid = "BENCH02YY0002";
  await (await registry.setVehicleOwner(certVid, owner.address)).wait();
  for (let i = 0; i < 3; i++) {
    const n = nonceFromSeed(100 + i);
    const ts = BigInt(Math.floor(Date.now() / 1000) + 300 + i);
    const sig = await mkSig(certVid, 90000n, 500n, 30n, 50n, 2n, ts, n);
    await (await registry.connect(station).storeEmission(
      certVid, 90000n, 500n, 30n, 50n, 2n, 1000n, 5000n, 0, ts, n, sig
    )).wait();
  }

  // 5. issueCertificate
  let issuedTokenId;
  {
    const tx = await puc.connect(station)
      ["issueCertificate(string,address,string)"](certVid, owner.address, "ipfs://QmBenchCert");
    const receipt = await tx.wait();
    const event = receipt.logs
      .map((l) => { try { return puc.interface.parseLog(l); } catch { return null; } })
      .find((e) => e && e.name === "CertificateIssued");
    issuedTokenId = event.args.tokenId;
    results.push(row("issueCertificate (with GreenToken mint)", "PUCCertificate", receipt.gasUsed));
  }

  // 6. revokeCertificate
  {
    const gas = await gasOf(puc.revokeCertificate(issuedTokenId, "gas benchmark"));
    results.push(row("revokeCertificate", "PUCCertificate", gas));
  }

  // 7. redeem (owner has reward tokens from cert issuance)
  {
    const bal = await greenToken.balanceOf(owner.address);
    if (bal >= ethers.parseEther("20")) {
      const gas = await gasOf(greenToken.connect(owner).redeem(3)); // PRIORITY_SERVICE = 20 GCT
      results.push(row("redeem (burn-to-reward)", "GreenToken", gas));
    }
  }

  // 8. Admin role updates
  results.push(row(
    "setTestingStation",
    "EmissionRegistry",
    await gasOf(registry.setTestingStation(owner.address, true))
  ));
  results.push(row(
    "setRegisteredDevice",
    "EmissionRegistry",
    await gasOf(registry.setRegisteredDevice(admin.address, true))
  ));
  results.push(row(
    "setVehicleOwner",
    "EmissionRegistry",
    await gasOf(registry.setVehicleOwner("BENCH03ZZ0003", owner.address))
  ));
  results.push(row(
    "setSoftVehicleCap",
    "EmissionRegistry",
    await gasOf(registry.setSoftVehicleCap(0))
  ));

  // Write JSON report
  const outDir = path.join(__dirname, "..", "docs");
  if (!fs.existsSync(outDir)) fs.mkdirSync(outDir, { recursive: true });
  const report = {
    generatedAt: new Date().toISOString(),
    toolchain: "hardhat+ethers-v6",
    proxyKind: "UUPS",
    assumptions: {
      polygon_gwei: POLYGON_GWEI,
      polygon_matic_usd: POLYGON_MATIC_USD,
      ethereum_gwei: ETH_GWEI,
      ethereum_usd: ETH_USD,
    },
    results,
  };
  fs.writeFileSync(path.join(outDir, "gas_report.json"), JSON.stringify(report, null, 2));

  console.log("\nGas usage report");
  console.log("================");
  console.log("Operation                                          | Contract         | Gas     | Polygon USD | ETH L1 USD");
  console.log("---------------------------------------------------|------------------|---------|-------------|------------");
  for (const r of results) {
    const op = (r.operation || "").padEnd(50);
    const c = (r.contract || "").padEnd(16);
    const g = String(r.gas).padStart(7);
    const p = ("$" + r.polygon_usd.toFixed(5)).padStart(11);
    const e = ("$" + r.ethereum_usd.toFixed(4)).padStart(10);
    console.log(`${op} | ${c} | ${g} | ${p} | ${e}`);
  }
  console.log("\nJSON written to docs/gas_report.json");
}

main()
  .then(() => process.exit(0))
  .catch((err) => {
    console.error(err);
    process.exit(1);
  });
