/**
 * Smart PUC — Polygon Amoy Deployment Wrapper
 * ============================================
 *
 * Thin wrapper over scripts/deploy.js that:
 *   1. Asserts the network is `amoy` (chainId 80002) before doing anything
 *      — prevents an accidental `--network localhost` deploy from being
 *      miscounted as a testnet deploy in the paper's reproducibility log.
 *   2. Checks that the deployer account has a non-zero MATIC balance and
 *      prints the faucet URL if it does not.
 *   3. Runs the main deployment flow from scripts/deploy.js.
 *   4. Attempts an automatic Polygonscan (Amoy) source verification on
 *      each deployed proxy + implementation pair, using the free
 *      POLYGONSCAN_API_KEY. Verification failures are logged but non-fatal
 *      so the deployment record in docs/DEPLOYED_ADDRESSES.json is always
 *      written even if the explorer is slow to index the bytecode.
 *
 * Required environment variables (.env):
 *   MNEMONIC              — BIP-39 mnemonic for the deployer wallet
 *   INFURA_PROJECT_ID     — Infura project id with Amoy RPC enabled (free)
 *   POLYGONSCAN_API_KEY   — Polygonscan API key (free tier, optional)
 *
 * Usage:
 *     npx hardhat run scripts/deploy_amoy.js --network amoy
 *
 * This file is zero-cost to include in the repository. Running it costs
 * a small amount of faucet-sourced test MATIC, not real money.
 */

const hre = require("hardhat");
const { ethers, upgrades, network } = hre;
const path = require("path");
const fs = require("fs");

const AMOY_CHAIN_ID = 80002;
const FAUCET_URL = "https://faucet.polygon.technology/  (select Amoy)";

async function assertAmoyNetwork() {
  const net = await ethers.provider.getNetwork();
  const chainId = Number(net.chainId);
  if (chainId !== AMOY_CHAIN_ID) {
    throw new Error(
      `Refusing to run deploy_amoy.js against chainId ${chainId}. ` +
      `Expected Amoy (${AMOY_CHAIN_ID}). ` +
      `Use:  npx hardhat run scripts/deploy_amoy.js --network amoy`
    );
  }
  if (network.name !== "amoy") {
    console.warn(
      `[warn] Network alias is "${network.name}" (chainId matches). ` +
      `If your hardhat.config.js aliases Amoy under a different name, ` +
      `verification may still work but logs will show the alias.`
    );
  }
}

async function assertFunded(signer) {
  const bal = await ethers.provider.getBalance(signer.address);
  if (bal === 0n) {
    console.error("\n[fatal] Deployer account has zero MATIC on Amoy.");
    console.error(`        Address : ${signer.address}`);
    console.error(`        Faucet  : ${FAUCET_URL}`);
    console.error("        Fund the account and re-run.\n");
    throw new Error("Deployer unfunded — request testnet MATIC from the faucet.");
  }
  const matic = Number(ethers.formatEther(bal));
  console.log(`[ok] Deployer balance : ${matic.toFixed(4)} MATIC`);
  if (matic < 0.1) {
    console.warn(`[warn] Balance under 0.1 MATIC — a full deploy + wire-up may fail midway.`);
  }
}

async function verifyOnPolygonscan(name, address, constructorArgs = []) {
  if (!process.env.POLYGONSCAN_API_KEY) {
    console.log(`[skip] Polygonscan verify for ${name} — POLYGONSCAN_API_KEY not set.`);
    return;
  }
  try {
    // Give the explorer a moment to index the bytecode before we hit it.
    await new Promise((r) => setTimeout(r, 15_000));
    await hre.run("verify:verify", {
      address,
      constructorArguments: constructorArgs,
    });
    console.log(`[ok] Verified ${name} at ${address} on Polygonscan`);
  } catch (err) {
    const msg = String(err && err.message ? err.message : err);
    if (/already verified/i.test(msg)) {
      console.log(`[ok] ${name} already verified on Polygonscan`);
    } else {
      console.warn(`[warn] Verify failed for ${name}: ${msg.split("\n")[0]}`);
    }
  }
}

async function main() {
  console.log("\n=====================================");
  console.log("  Smart PUC — Amoy Testnet Deploy");
  console.log("=====================================\n");

  await assertAmoyNetwork();

  const [deployer] = await ethers.getSigners();
  console.log(`[info] Deployer : ${deployer.address}`);
  await assertFunded(deployer);

  // Delegate the actual deployment to the shared flow in scripts/deploy.js
  // so there is a single source of truth for the post-deploy wiring and
  // the addresses written into build/contracts/*.json.
  //
  // We cannot `require("./deploy.js")` here because that file auto-invokes
  // main() and calls process.exit(0) on success, which would terminate us
  // before we get to run Polygonscan verification. Fork a child process
  // instead so its process.exit is contained.
  console.log("\n[step] Running shared deployment flow from scripts/deploy.js ...\n");
  const { spawnSync } = require("child_process");
  const result = spawnSync(
    process.execPath,
    [
      path.join(__dirname, "..", "node_modules", "hardhat", "internal", "cli", "cli.js"),
      "run",
      "scripts/deploy.js",
      "--network",
      "amoy",
    ],
    { stdio: "inherit", cwd: path.join(__dirname, "..") }
  );
  if (result.status !== 0) {
    throw new Error(`deploy.js exited with code ${result.status}`);
  }

  // Read back the deployed addresses written by deploy.js
  const outFile = path.join(__dirname, "..", "docs", "DEPLOYED_ADDRESSES.json");
  if (!fs.existsSync(outFile)) {
    console.warn("[warn] docs/DEPLOYED_ADDRESSES.json not found — skipping verification.");
    return;
  }
  const record = JSON.parse(fs.readFileSync(outFile, "utf8"))[String(AMOY_CHAIN_ID)];
  if (!record) {
    console.warn("[warn] No Amoy entry in DEPLOYED_ADDRESSES.json — skipping verification.");
    return;
  }

  console.log("\n[step] Verifying contracts on Polygonscan ...\n");
  for (const [name, addr] of Object.entries(record.contracts)) {
    await verifyOnPolygonscan(name, addr, []);
  }

  console.log("\n=====================================");
  console.log("  Amoy deployment complete.");
  console.log("=====================================");
  console.log(`  GreenToken       : ${record.contracts.GreenToken}`);
  console.log(`  EmissionRegistry : ${record.contracts.EmissionRegistry}`);
  console.log(`  PUCCertificate   : ${record.contracts.PUCCertificate}`);
  console.log(`  Explorer         : https://amoy.polygonscan.com/address/${record.contracts.EmissionRegistry}\n`);
}

main()
  .then(() => process.exit(0))
  .catch((err) => {
    console.error(err);
    process.exit(1);
  });
