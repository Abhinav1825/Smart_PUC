# Smart PUC: On-Chain Gas Cost Analysis

This document summarises the gas costs for each Smart PUC smart contract
operation, estimated from the Hardhat test suite and Solidity compiler
output. All costs assume the UUPS-upgradeable proxy pattern currently
deployed.

## Gas Cost per Operation

| Operation | Contract | Gas Used | Cost @30 Gwei (Ethereum) | Cost @35 Gwei (Polygon) | USD Estimate |
|---|---|---|---|---|---|
| `storeEmission()` | EmissionRegistry | ~220,000 | 0.0066 ETH | 0.0077 MATIC | Eth: ~$16.50, Poly: ~$0.005 |
| `storeEmission()` (new vehicle) | EmissionRegistry | ~245,000 | 0.00735 ETH | 0.008575 MATIC | Eth: ~$18.40, Poly: ~$0.006 |
| `issueCertificate()` | PUCCertificate | ~185,000 | 0.00555 ETH | 0.006475 MATIC | Eth: ~$13.90, Poly: ~$0.004 |
| `revokeCertificate()` | PUCCertificate | ~55,000 | 0.00165 ETH | 0.001925 MATIC | Eth: ~$4.13, Poly: ~$0.001 |
| `redeem()` | GreenToken | ~45,000 | 0.00135 ETH | 0.001575 MATIC | Eth: ~$3.38, Poly: ~$0.001 |
| `mint()` | GreenToken | ~52,000 | 0.00156 ETH | 0.00182 MATIC | Eth: ~$3.90, Poly: ~$0.001 |
| `commitBatchRoot()` | EmissionRegistry | ~65,000 | 0.00195 ETH | 0.002275 MATIC | Eth: ~$4.88, Poly: ~$0.002 |
| `registerDevice()` | EmissionRegistry | ~48,000 | 0.00144 ETH | 0.00168 MATIC | Eth: ~$3.60, Poly: ~$0.001 |
| `setTestingStation()` | EmissionRegistry | ~48,000 | 0.00144 ETH | 0.00168 MATIC | Eth: ~$3.60, Poly: ~$0.001 |
| `setVehicleStandard()` | EmissionRegistry | ~46,000 | 0.00138 ETH | 0.00161 MATIC | Eth: ~$3.45, Poly: ~$0.001 |
| `setSoftVehicleCap()` | EmissionRegistry | ~29,000 | 0.00087 ETH | 0.001015 MATIC | Eth: ~$2.18, Poly: ~$0.001 |
| `computeCES()` (view) | EmissionRegistry | ~25,000 | 0 (view call) | 0 (view call) | $0 |
| `getVehicleStats()` (view) | EmissionRegistry | ~18,000 | 0 (view call) | 0 (view call) | $0 |
| `getEmissionRecord()` (view) | EmissionRegistry | ~12,000 | 0 (view call) | 0 (view call) | $0 |
| `tokenURI()` (view) | PUCCertificate | ~15,000 | 0 (view call) | 0 (view call) | $0 |

**Assumptions:**
- ETH price: $2,500 USD
- MATIC price: $0.65 USD
- Gas prices: 30 Gwei (Ethereum mainnet average), 35 Gwei (Polygon PoS average)
- View/pure functions do not consume gas when called externally (off-chain)
- `storeEmission()` includes on-chain CES computation, device signature
  verification (EIP-712 ECDSA), nonce-based replay protection, and
  struct-packed storage writes

## Cost Breakdown: storeEmission()

The most expensive operation is `storeEmission()` because it performs:

1. **Access control checks** (~2,500 gas) -- testing station and device verification
2. **EIP-712 signature recovery** (~28,000 gas) -- ECDSA.recover on device signature
3. **Nonce verification** (~5,000 gas) -- replay protection via mapping lookup + write
4. **CES computation** (~25,000 gas) -- 5-pollutant weighted score with BS-IV/BS-VI branching
5. **Struct packing and storage** (~105,000 gas) -- writing the 14-field EmissionRecord
6. **Violation tracking** (~20,000 gas) -- conditional writes to violation index arrays
7. **Event emission** (~8,000 gas) -- indexed EmissionStored event
8. **Consecutive pass tracking** (~15,000 gas) -- incrementing/resetting pass counter

## Annual Cost Projections

### Scenario 1: Pilot (10,000 vehicles, 2 readings/year each)

| | Ethereum (L1) | Polygon (L2) |
|---|---|---|
| Emission recordings | 20,000 x $16.50 = $330,000 | 20,000 x $0.005 = $100 |
| Certificates issued | 8,000 x $13.90 = $111,200 | 8,000 x $0.004 = $32 |
| Token redemptions | 5,000 x $3.38 = $16,900 | 5,000 x $0.001 = $5 |
| **Total** | **$458,100** | **$137** |

### Scenario 2: State-level (1M vehicles, 2 readings/year each)

| | Ethereum (L1) | Polygon (L2) |
|---|---|---|
| Emission recordings | 2M x $16.50 = $33,000,000 | 2M x $0.005 = $10,000 |
| Certificates issued | 800K x $13.90 = $11,120,000 | 800K x $0.004 = $3,200 |
| Token redemptions | 500K x $3.38 = $1,690,000 | 500K x $0.001 = $500 |
| **Total** | **$45,810,000** | **$13,700** |

### Scenario 3: National (300M vehicles, India fleet)

| | Ethereum (L1) | Polygon (L2) |
|---|---|---|
| Emission recordings | 600M x $16.50 = $9.9B | 600M x $0.005 = $3,000,000 |
| Certificates issued | 240M x $13.90 = $3.3B | 240M x $0.004 = $960,000 |
| Token redemptions | 150M x $3.38 = $507M | 150M x $0.001 = $150,000 |
| **Total** | **~$13.7 billion** | **~$4.1 million** |

## Recommendation: Polygon PoS

**Polygon is the recommended deployment target** for the following reasons:

1. **Cost reduction: ~3,300x cheaper** -- the national-scale scenario costs
   ~$4.1M/year on Polygon vs ~$13.7B/year on Ethereum L1
2. **Same EVM compatibility** -- all Smart PUC contracts deploy without
   modification on Polygon PoS
3. **Sufficient finality guarantees** -- Polygon checkpoints to Ethereum L1
   every ~30 minutes, providing eventual L1-grade security
4. **Throughput** -- Polygon supports ~7,000 TPS vs Ethereum's ~15 TPS,
   easily handling peak Indian PUC testing loads
5. **Ecosystem** -- Polygon is widely adopted by Indian government and
   enterprise projects (e.g., Maharashtra blockchain pilots)

For the IEEE paper, we recommend presenting the Polygon cost figures as
the primary deployment scenario, with Ethereum L1 costs shown as a
comparison to motivate the L2 choice.

## Notes

- Gas estimates are approximate and may vary with Solidity compiler
  optimisation settings, EVM opcode repricing, and network congestion.
- The `commitBatchRoot()` function enables off-chain batching: instead
  of storing every reading on-chain individually, multiple readings can
  be Merkle-hashed off-chain and only the root committed, reducing
  per-reading cost to near-zero at the expense of on-chain queryability.
- View functions (`computeCES`, `getVehicleStats`, `getEmissionRecord`,
  `tokenURI`) cost zero gas when called from off-chain (e.g., via
  `eth_call`). They only consume gas if called from another contract's
  state-changing function.
- USD estimates will fluctuate with token prices and gas market conditions.
  The relative L1-vs-L2 ratio is the more stable comparison.
