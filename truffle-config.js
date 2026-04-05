/**
 * Smart PUC — Truffle Configuration
 *
 * Networks:
 *   - development : Ganache on localhost:7545
 *   - sepolia     : Sepolia testnet via Infura (requires .env)
 *   - polygon     : Polygon mainnet via Infura (requires .env)
 *   - amoy        : Polygon Amoy testnet via Infura (requires .env)
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
