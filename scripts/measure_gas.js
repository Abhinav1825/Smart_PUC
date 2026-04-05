/**
 * Smart PUC — Gas Measurement Harness
 * ====================================
 *
 * Measures gas usage of every state-changing operation in the three
 * contracts (EmissionRegistry, PUCCertificate, GreenToken) and writes the
 * results to docs/gas_report.json plus a human-readable Markdown table to
 * stdout.
 *
 * Usage:
 *     npx truffle exec scripts/measure_gas.js --network development
 *
 * Prerequisites:
 *     - Ganache running on localhost:7545 (deterministic accounts)
 *     - `truffle migrate --reset` has been executed at least once
 */

const fs = require("fs");
const path = require("path");

const EmissionRegistry = artifacts.require("EmissionRegistry");
const PUCCertificate = artifacts.require("PUCCertificate");
const GreenToken = artifacts.require("GreenToken");

// Gas price assumptions for fiat conversion (update as needed)
const POLYGON_GWEI = 50;
const POLYGON_MATIC_USD = 0.70;
const ETH_GWEI = 15;
const ETH_USD = 2400;

function gasToUsd(gas, gwei, tokenUsd) {
  const ethPerGas = gwei * 1e-9;
  const ethCost = gas * ethPerGas;
  return ethCost * tokenUsd;
}

function row(op, contract, gas) {
  const polygon = gasToUsd(gas, POLYGON_GWEI, POLYGON_MATIC_USD);
  const eth = gasToUsd(gas, ETH_GWEI, ETH_USD);
  return {
    operation: op,
    contract: contract,
    gas: gas,
    polygon_usd: Number(polygon.toFixed(6)),
    ethereum_usd: Number(eth.toFixed(4)),
  };
}

module.exports = async function (callback) {
  try {
    const accounts = await web3.eth.getAccounts();
    const admin = accounts[0];
    const station = accounts[1];
    const device = accounts[2];
    const owner = accounts[3];

    const registry = await EmissionRegistry.deployed();
    const puc = await PUCCertificate.deployed();
    const green = await GreenToken.deployed();

    // Ensure roles are set
    try { await registry.setTestingStation(station, true, { from: admin }); } catch (_) {}
    try { await registry.setRegisteredDevice(device, true, { from: admin }); } catch (_) {}
    try { await registry.setVehicleOwner("BENCH01XX0001", owner, { from: admin }); } catch (_) {}

    const results = [];

    // Helper: produce a signed emission payload from the device
    const signPayload = async (vehicleId, nonceHex) => {
      const co2 = 110000, co = 800, nox = 50, hc = 80, pm25 = 4;
      const timestamp = Math.floor(Date.now() / 1000);
      const messageHash = await registry.getMessageHash(
        vehicleId, co2, co, nox, hc, pm25, timestamp, nonceHex
      );
      const sig = await web3.eth.sign(messageHash, device);
      // web3.eth.sign prepends the Ethereum-signed-message prefix automatically
      return { co2, co, nox, hc, pm25, timestamp, sig };
    };

    const mkNonce = (i) =>
      web3.utils.padLeft(web3.utils.toHex("gasbench-" + Date.now() + "-" + i), 64);

    // 1. storeEmission — first submission (new vehicle)
    {
      const vid = "BENCH01XX0001";
      const nonce = mkNonce(1);
      const { co2, co, nox, hc, pm25, timestamp, sig } = await signPayload(vid, nonce);
      const tx = await registry.storeEmission(
        vid, co2, co, nox, hc, pm25, 1000, 5000, 0, timestamp, nonce, sig,
        { from: station }
      );
      results.push(row("storeEmission (first submission for new vehicle)", "EmissionRegistry", tx.receipt.gasUsed));
    }

    // 2. storeEmission — subsequent PASS
    {
      const vid = "BENCH01XX0001";
      const nonce = mkNonce(2);
      const { co2, co, nox, hc, pm25, timestamp, sig } = await signPayload(vid, nonce);
      const tx = await registry.storeEmission(
        vid, co2, co, nox, hc, pm25, 1000, 5000, 0, timestamp, nonce, sig,
        { from: station }
      );
      results.push(row("storeEmission (subsequent PASS)", "EmissionRegistry", tx.receipt.gasUsed));
    }

    // 3. storeEmission — FAIL (emits violation events)
    {
      const vid = "BENCH01XX0001";
      const nonce = mkNonce(3);
      const co2 = 200000, co = 2500, nox = 300, hc = 300, pm25 = 20;
      const timestamp = Math.floor(Date.now() / 1000);
      const messageHash = await registry.getMessageHash(vid, co2, co, nox, hc, pm25, timestamp, nonce);
      const sig = await web3.eth.sign(messageHash, device);
      const tx = await registry.storeEmission(
        vid, co2, co, nox, hc, pm25, 7000, 8000, 1, timestamp, nonce, sig,
        { from: station }
      );
      results.push(row("storeEmission (FAIL + pollutant events)", "EmissionRegistry", tx.receipt.gasUsed));
    }

    // 4. Produce three consecutive PASS records for a second vehicle to enable cert issuance
    const certVid = "BENCH02YY0002";
    await registry.setVehicleOwner(certVid, owner, { from: admin }).catch(() => {});
    for (let i = 0; i < 3; i++) {
      const nonce = mkNonce("cert" + i);
      const { co2, co, nox, hc, pm25, timestamp, sig } = await signPayload(certVid, nonce);
      await registry.storeEmission(
        certVid, co2, co, nox, hc, pm25, 1000, 5000, 0, timestamp, nonce, sig,
        { from: station }
      );
    }

    // 5. issueCertificate
    {
      const tx = await puc.methods["issueCertificate(string,address,string)"](
        certVid, owner, "ipfs://QmBenchCertMeta",
        { from: admin }
      );
      results.push(row("issueCertificate (with GreenToken mint)", "PUCCertificate", tx.receipt.gasUsed));
    }

    // 6. revokeCertificate
    {
      const tokenId = await puc.getVehicleCertificate(certVid);
      const tx = await puc.revokeCertificate(tokenId, "gas benchmark", { from: admin });
      results.push(row("revokeCertificate", "PUCCertificate", tx.receipt.gasUsed));
    }

    // 7. redeem (burn-to-reward). Owner must have tokens from cert issuance.
    {
      try {
        const balance = await green.balanceOf(owner);
        if (balance.gte(web3.utils.toBN("20000000000000000000"))) {
          const tx = await green.redeem(3, { from: owner }); // PRIORITY_SERVICE = 20 GCT
          results.push(row("redeem (burn-to-reward)", "GreenToken", tx.receipt.gasUsed));
        } else {
          results.push({ operation: "redeem (burn-to-reward)", contract: "GreenToken", gas: null, note: "skipped — insufficient balance" });
        }
      } catch (e) {
        results.push({ operation: "redeem (burn-to-reward)", contract: "GreenToken", gas: null, note: "error: " + e.message });
      }
    }

    // 8. Admin role updates (amortised)
    {
      const tx = await registry.setTestingStation(accounts[4], true, { from: admin });
      results.push(row("setTestingStation", "EmissionRegistry", tx.receipt.gasUsed));
    }
    {
      const tx = await registry.setRegisteredDevice(accounts[5], true, { from: admin });
      results.push(row("setRegisteredDevice", "EmissionRegistry", tx.receipt.gasUsed));
    }
    {
      const tx = await registry.setVehicleOwner("BENCH03ZZ0003", owner, { from: admin });
      results.push(row("setVehicleOwner", "EmissionRegistry", tx.receipt.gasUsed));
    }

    // Write outputs
    const outDir = path.join(__dirname, "..", "docs");
    if (!fs.existsSync(outDir)) fs.mkdirSync(outDir, { recursive: true });
    const report = {
      generatedAt: new Date().toISOString(),
      assumptions: {
        polygon_gwei: POLYGON_GWEI,
        polygon_matic_usd: POLYGON_MATIC_USD,
        ethereum_gwei: ETH_GWEI,
        ethereum_usd: ETH_USD,
      },
      results: results,
    };
    fs.writeFileSync(
      path.join(outDir, "gas_report.json"),
      JSON.stringify(report, null, 2)
    );

    // Pretty print
    console.log("\nGas usage report");
    console.log("================");
    console.log("Operation                                         | Contract           | Gas     | Polygon USD | ETH L1 USD");
    console.log("---------------------------------------------------|--------------------|---------|-------------|------------");
    for (const r of results) {
      const op = (r.operation || "").padEnd(50);
      const c  = (r.contract || "").padEnd(18);
      const g  = (r.gas == null ? "-" : String(r.gas)).padStart(7);
      const p  = (r.polygon_usd == null ? "-" : "$" + r.polygon_usd.toFixed(5)).padStart(11);
      const e  = (r.ethereum_usd == null ? "-" : "$" + r.ethereum_usd.toFixed(4)).padStart(10);
      console.log(`${op} | ${c} | ${g} | ${p} | ${e}`);
    }
    console.log(`\nJSON written to docs/gas_report.json`);

    callback();
  } catch (err) {
    console.error(err);
    callback(err);
  }
};
