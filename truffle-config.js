/**
 * Smart PUC — Truffle Configuration
 *
 * Networks:
 *   - development    : Ganache on localhost:7545
 *   - sepolia        : Sepolia testnet via Infura (requires .env)
 *   - polygon        : Polygon PoS mainnet via Infura (requires .env)
 *   - amoy           : Polygon Amoy testnet via Infura (requires .env)
 *   - zkevm          : Polygon zkEVM mainnet via public RPC (requires .env)
 *   - zkevm_cardona  : Polygon zkEVM Cardona testnet via public RPC
 *   - arbitrum       : Arbitrum One mainnet via public RPC (requires .env)
 *   - arbitrum_sepolia : Arbitrum Sepolia testnet via public RPC
 *
 * NOTE: The zkEVM and Arbitrum entries are provided for reproducibility of
 * the gas cost projections in docs/GAS_ANALYSIS.md. They have not yet been
 * end-to-end tested from this repository; see docs/ARCHITECTURE_TRADEOFFS.md
 * §7 for the current status.
 */

require("dotenv").config();

const HDWalletProvider = require("@truffle/hdwallet-provider");

module.exports = {
  networks: {
    // ─── Local Ganache ──────────────────────────────────────────────────
    development: {
      host: "127.0.0.1",
      port: 7545,
      network_id: "*", // Match any network id
    },

    // ─── Sepolia Testnet ────────────────────────────────────────────────
    sepolia: {
      provider: () =>
        new HDWalletProvider(
          process.env.MNEMONIC,
          `https://sepolia.infura.io/v3/${process.env.INFURA_PROJECT_ID}`
        ),
      network_id: 11155111, // Sepolia chain id
      gas: 5500000,
      confirmations: 2,
      timeoutBlocks: 200,
      skipDryRun: true,
    },

    // ─── Polygon Mainnet ────────────────────────────────────────────────
    polygon: {
      provider: () =>
        new HDWalletProvider(
          process.env.MNEMONIC,
          `https://polygon-mainnet.infura.io/v3/${process.env.INFURA_PROJECT_ID}`
        ),
      network_id: 137, // Polygon mainnet chain id
      gas: 5500000,
      gasPrice: 35000000000, // 35 gwei
      confirmations: 2,
      timeoutBlocks: 200,
      skipDryRun: false,
    },

    // ─── Polygon Amoy Testnet ───────────────────────────────────────────
    amoy: {
      provider: () =>
        new HDWalletProvider(
          process.env.MNEMONIC,
          `https://polygon-amoy.infura.io/v3/${process.env.INFURA_PROJECT_ID}`
        ),
      network_id: 80002, // Polygon Amoy chain id
      gas: 5500000,
      confirmations: 2,
      timeoutBlocks: 200,
      skipDryRun: true,
    },

    // ─── Polygon zkEVM Mainnet ──────────────────────────────────────────
    zkevm: {
      provider: () =>
        new HDWalletProvider(
          process.env.MNEMONIC,
          "https://zkevm-rpc.com"
        ),
      network_id: 1101, // Polygon zkEVM mainnet
      gas: 5500000,
      confirmations: 2,
      timeoutBlocks: 200,
      skipDryRun: false,
    },

    // ─── Polygon zkEVM Cardona Testnet ──────────────────────────────────
    zkevm_cardona: {
      provider: () =>
        new HDWalletProvider(
          process.env.MNEMONIC,
          "https://rpc.cardona.zkevm-rpc.com"
        ),
      network_id: 2442, // Cardona testnet
      gas: 5500000,
      confirmations: 2,
      timeoutBlocks: 200,
      skipDryRun: true,
    },

    // ─── Arbitrum One Mainnet ───────────────────────────────────────────
    arbitrum: {
      provider: () =>
        new HDWalletProvider(
          process.env.MNEMONIC,
          "https://arb1.arbitrum.io/rpc"
        ),
      network_id: 42161, // Arbitrum One
      gas: 5500000,
      confirmations: 2,
      timeoutBlocks: 200,
      skipDryRun: false,
    },

    // ─── Arbitrum Sepolia Testnet ───────────────────────────────────────
    arbitrum_sepolia: {
      provider: () =>
        new HDWalletProvider(
          process.env.MNEMONIC,
          "https://sepolia-rollup.arbitrum.io/rpc"
        ),
      network_id: 421614, // Arbitrum Sepolia
      gas: 5500000,
      confirmations: 2,
      timeoutBlocks: 200,
      skipDryRun: true,
    },
  },

  // ─── Compiler ───────────────────────────────────────────────────────
  compilers: {
    solc: {
      version: "0.8.21",
      settings: {
        viaIR: true,
        optimizer: {
          enabled: true,
          runs: 200,
        },
      },
    },
  },

  // ─── Build directory (so backend can find the ABI) ──────────────────
  contracts_build_directory: "./build/contracts",
};
