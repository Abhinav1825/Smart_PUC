/**
 * Smart PUC — Hardhat Configuration
 * =================================
 *
 * Replaces the legacy Truffle toolchain. Preserves full behavioural
 * compatibility with the rest of the project by writing a
 * Truffle-shaped JSON artifact (with the `networks` map) into
 * `build/contracts/<Name>.json` after every deploy, so that the Python
 * backend (`backend/blockchain_connector.py`) keeps working without any
 * change.
 *
 * Networks:
 *   - hardhat           : In-process EVM (for `npx hardhat test`)
 *   - localhost         : External Ganache/Hardhat-node on :7545 (CHAIN_ID=5777)
 *   - sepolia           : Ethereum testnet via Infura
 *   - polygon           : Polygon PoS mainnet via Infura
 *   - amoy              : Polygon Amoy testnet via Infura
 *   - zkevm             : Polygon zkEVM mainnet (public RPC)
 *   - zkevm_cardona     : Polygon zkEVM Cardona testnet (public RPC)
 *   - arbitrum          : Arbitrum One mainnet (public RPC)
 *   - arbitrum_sepolia  : Arbitrum Sepolia testnet (public RPC)
 *
 * NOTE: The zkEVM and Arbitrum entries are provided for reproducibility
 * of the gas-cost projections in docs/GAS_ANALYSIS.md but have not yet
 * been end-to-end tested from this repository.
 */

require("@nomicfoundation/hardhat-toolbox");
require("@openzeppelin/hardhat-upgrades");
require("dotenv").config();

const MNEMONIC = process.env.MNEMONIC || "";
const INFURA = process.env.INFURA_PROJECT_ID || "";

// HDWallet accounts entry shared by every remote network.
const hdAccounts = MNEMONIC
  ? { mnemonic: MNEMONIC, path: "m/44'/60'/0'/0", initialIndex: 0, count: 10 }
  : undefined;

/** @type import("hardhat/config").HardhatUserConfig */
module.exports = {
  solidity: {
    version: "0.8.21",
    settings: {
      viaIR: true,  // Required — EmissionRegistry has stack-too-deep without IR
      optimizer: { enabled: true, runs: 200 },
    },
  },

  paths: {
    // Keep the Hardhat defaults but ALSO flatten to build/contracts via
    // scripts/flatten_artifacts.js after every compile (see package.json).
    sources: "./contracts",
    tests: "./test",
    cache: "./cache",
    artifacts: "./artifacts",
  },

  networks: {
    // ─── In-process EVM used by `npx hardhat test` ────────────────────
    hardhat: {
      chainId: 31337,
      // Match Ganache's deterministic test mnemonic so signatures line up
      // across local Ganache runs and the in-process Hardhat Network runs.
      accounts: {
        mnemonic: "myth like bonus scare over problem client lid proud cousin toddler paragraph",
        accountsBalance: "100000000000000000000", // 100 ETH
        count: 10,
      },
      blockGasLimit: 12_000_000,
      allowUnlimitedContractSize: false,
    },

    // ─── External Ganache / Hardhat node on :7545 ─────────────────────
    localhost: {
      url: process.env.RPC_URL || "http://127.0.0.1:7545",
      chainId: Number(process.env.CHAIN_ID || 5777),
      timeout: 120_000, // 2 minutes — UUPS proxy deploys can be slow
      // `accounts` is intentionally omitted — Ganache exposes its own
      // accounts over JSON-RPC and the signer list is picked up via
      // `ethers.getSigners()` when targeting an external node.
    },

    // ─── Docker-compose wiring ─────────────────────────────────────────
    // Used by the `deploy-contracts` service; RPC_URL defaults to the
    // internal ganache service DNS name.
    docker: {
      url: process.env.RPC_URL || "http://ganache:8545",
      chainId: 5777,
      timeout: 120_000,
    },

    // ─── Ethereum Sepolia ─────────────────────────────────────────────
    sepolia: {
      url: `https://sepolia.infura.io/v3/${INFURA}`,
      chainId: 11155111,
      accounts: hdAccounts,
      gas: 5_500_000,
      timeout: 60_000,
    },

    // ─── Polygon PoS Mainnet ──────────────────────────────────────────
    polygon: {
      url: `https://polygon-mainnet.infura.io/v3/${INFURA}`,
      chainId: 137,
      accounts: hdAccounts,
      gas: 5_500_000,
      gasPrice: 35_000_000_000,
      timeout: 120_000,
    },

    // ─── Polygon Amoy Testnet ─────────────────────────────────────────
    amoy: {
      url: `https://polygon-amoy.infura.io/v3/${INFURA}`,
      chainId: 80002,
      accounts: hdAccounts,
      gas: 5_500_000,
      timeout: 60_000,
    },

    // ─── Polygon zkEVM Mainnet ────────────────────────────────────────
    zkevm: {
      url: "https://zkevm-rpc.com",
      chainId: 1101,
      accounts: hdAccounts,
      gas: 5_500_000,
      timeout: 120_000,
    },

    // ─── Polygon zkEVM Cardona Testnet ────────────────────────────────
    zkevm_cardona: {
      url: "https://rpc.cardona.zkevm-rpc.com",
      chainId: 2442,
      accounts: hdAccounts,
      gas: 5_500_000,
      timeout: 60_000,
    },

    // ─── Arbitrum One ─────────────────────────────────────────────────
    arbitrum: {
      url: "https://arb1.arbitrum.io/rpc",
      chainId: 42161,
      accounts: hdAccounts,
      gas: 5_500_000,
      timeout: 120_000,
    },

    // ─── Arbitrum Sepolia ─────────────────────────────────────────────
    arbitrum_sepolia: {
      url: "https://sepolia-rollup.arbitrum.io/rpc",
      chainId: 421614,
      accounts: hdAccounts,
      gas: 5_500_000,
      timeout: 60_000,
    },
  },

  mocha: {
    timeout: 120_000,
  },

  // Polygonscan source verification used by scripts/deploy_amoy.js.
  // The API key is free from https://polygonscan.com/myapikey. If the
  // environment variable is missing the verify step in deploy_amoy.js is
  // a no-op, so the repository remains fully usable without it.
  etherscan: {
    apiKey: {
      polygon:          process.env.POLYGONSCAN_API_KEY || "",
      polygonAmoy:      process.env.POLYGONSCAN_API_KEY || "",
      polygonZkEVM:     process.env.POLYGONSCAN_API_KEY || "",
    },
    customChains: [
      {
        network: "polygonAmoy",
        chainId: 80002,
        urls: {
          apiURL: "https://api-amoy.polygonscan.com/api",
          browserURL: "https://amoy.polygonscan.com",
        },
      },
    ],
  },

  gasReporter: {
    enabled: process.env.REPORT_GAS ? true : false,
    currency: "USD",
    gasPrice: 50, // gwei, matches docs/GAS_ANALYSIS.md assumption
    showTimeSpent: true,
    excludeContracts: [],
    outputFile: process.env.GAS_REPORT_OUTPUT || undefined,
  },
};
