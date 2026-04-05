/**
 * Smart PUC — Truffle-Compat Artifact Flattener
 * ===============================================
 *
 * Hardhat writes compiled artifacts to
 *     artifacts/contracts/<Name>.sol/<Name>.json
 *
 * The Python backend (backend/blockchain_connector.py) expects the
 * legacy Truffle layout:
 *     build/contracts/<Name>.json  ← flat file, shape:
 *     {
 *       "abi": [...],
 *       "bytecode": "0x...",
 *       "networks": { "<chainId>": { "address": "0x..." } }
 *     }
 *
 * This script copies every contract's Hardhat artifact into that flat
 * shape so the backend keeps working without a single line of Python
 * change. The `networks` field is preserved across runs — we read the
 * existing file (if any) and merge, so running the deploy script does
 * not clobber previously recorded addresses on other chains.
 *
 * Usage:
 *     node scripts/flatten_artifacts.js
 *
 * Usually invoked automatically by the `compile` / `postdeploy` npm
 * scripts in package.json.
 */

const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const HARDHAT_ARTIFACTS = path.join(ROOT, "artifacts", "contracts");
const TARGET_DIR = path.join(ROOT, "build", "contracts");

// Only flatten these top-level contracts (ignore interfaces and libs).
const INCLUDED = new Set([
  "EmissionRegistry",
  "PUCCertificate",
  "GreenToken",
]);

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function writeJson(file, obj) {
  fs.writeFileSync(file, JSON.stringify(obj, null, 2));
}

function ensureDir(dir) {
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
}

function flatten() {
  if (!fs.existsSync(HARDHAT_ARTIFACTS)) {
    console.error(`[flatten_artifacts] Hardhat artifacts not found at ${HARDHAT_ARTIFACTS}. Run 'npx hardhat compile' first.`);
    process.exit(1);
  }
  ensureDir(TARGET_DIR);

  let written = 0;
  for (const entry of fs.readdirSync(HARDHAT_ARTIFACTS, { withFileTypes: true })) {
    if (!entry.isDirectory() || !entry.name.endsWith(".sol")) continue;
    const contractDir = path.join(HARDHAT_ARTIFACTS, entry.name);
    for (const file of fs.readdirSync(contractDir)) {
      if (!file.endsWith(".json") || file.endsWith(".dbg.json")) continue;
      const name = file.replace(/\.json$/, "");
      if (!INCLUDED.has(name)) continue;

      const src = readJson(path.join(contractDir, file));
      const dstPath = path.join(TARGET_DIR, `${name}.json`);
      // Preserve any existing `networks` entries so we don't lose the
      // deployed addresses recorded on previous runs.
      let existingNetworks = {};
      if (fs.existsSync(dstPath)) {
        try {
          const existing = readJson(dstPath);
          existingNetworks = existing.networks || {};
        } catch (_e) {
          /* ignore — fresh file will overwrite */
        }
      }

      const flat = {
        contractName: src.contractName || name,
        abi: src.abi,
        bytecode: src.bytecode,
        deployedBytecode: src.deployedBytecode,
        networks: existingNetworks,
      };
      writeJson(dstPath, flat);
      written += 1;
    }
  }
  console.log(`[flatten_artifacts] wrote ${written} contract(s) to build/contracts/`);
}

/**
 * Record a deployed address into the flat artifact's `networks` map.
 * Called by scripts/deploy.js after each proxy deployment.
 *
 * @param {string} name       Contract name (e.g. "EmissionRegistry")
 * @param {number|string} chainId Chain id as a number or string
 * @param {string} address    Deployed proxy address
 */
function recordAddress(name, chainId, address) {
  ensureDir(TARGET_DIR);
  const file = path.join(TARGET_DIR, `${name}.json`);
  if (!fs.existsSync(file)) {
    throw new Error(`Cannot record address — ${name}.json not found. Run flatten first.`);
  }
  const obj = readJson(file);
  obj.networks = obj.networks || {};
  obj.networks[String(chainId)] = {
    address: address,
    transactionHash: obj.networks[String(chainId)]?.transactionHash || null,
  };
  writeJson(file, obj);
}

module.exports = { flatten, recordAddress };

if (require.main === module) {
  flatten();
}
