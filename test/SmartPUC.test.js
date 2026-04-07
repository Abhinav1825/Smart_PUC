/**
 * Smart PUC — Hardhat + ethers v6 test suite.
 *
 * Replaces the legacy Truffle-based test/TestEmission.js. Every
 * contract is deployed as a UUPS proxy via the OpenZeppelin upgrades
 * plugin, matching the production deployment path (scripts/deploy.js).
 *
 * Test cases TC-01..TC-30 preserve the coverage of the Truffle suite;
 * we also add TC-31..TC-33 exercising proxy semantics.
 */

const { expect } = require("chai");
const { ethers, upgrades } = require("hardhat");

// ────────────────────────── helpers ────────────────────────────────────

function nonceFromSeed(seed) {
  // Truffle version used soliditySha3(uint256). ethers v6 equivalent:
  return ethers.keccak256(ethers.AbiCoder.defaultAbiCoder().encode(["uint256"], [seed]));
}

/**
 * Sign an emission payload using EIP-712 typed data, matching the
 * EmissionReading struct verified by EmissionRegistry._verifyDeviceSignature.
 * The domain binds (chainId, verifyingContract) which mitigates cross-chain
 * replay (A9 in the threat model).
 */
async function signEmission(signer, registry, vehicleId, co2, co, nox, hc, pm25, timestamp, nonce) {
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
  const value = { vehicleId, co2, co, nox, hc, pm25, timestamp, nonce };
  return signer.signTypedData(domain, types, value);
}

/**
 * Sign a VehicleClaim payload (admin → claimant binding for claimVehicle).
 */
async function signVehicleClaim(adminSigner, registry, vehicleId, claimant) {
  const net = await ethers.provider.getNetwork();
  const domain = {
    name: "SmartPUC",
    version: "3.2",
    chainId: net.chainId,
    verifyingContract: await registry.getAddress(),
  };
  const types = {
    VehicleClaim: [
      { name: "vehicleId", type: "string" },
      { name: "claimant", type: "address" },
    ],
  };
  return adminSigner.signTypedData(domain, types, { vehicleId, claimant });
}

async function storePassRecord(registry, vehicleId, stationSigner, deviceSigner, nonceSeed, timestamp) {
  const co2 = 80000n;
  const co = 500n;
  const nox = 30n;
  const hc = 50n;
  const pm25 = 2n;
  const fraudScore = 1000n;
  const vspValue = 15000n;
  const wltcPhase = 2;
  const ts = BigInt(timestamp || 1700000000);
  const n = nonceFromSeed(nonceSeed);
  const sig = await signEmission(deviceSigner, registry, vehicleId, co2, co, nox, hc, pm25, ts, n);
  return registry
    .connect(stationSigner)
    .storeEmission(vehicleId, co2, co, nox, hc, pm25, fraudScore, vspValue, wltcPhase, ts, n, sig);
}

async function storeFailRecord(registry, vehicleId, stationSigner, deviceSigner, nonceSeed, timestamp) {
  const co2 = 200000n;
  const co = 2000n;
  const nox = 120n;
  const hc = 200n;
  const pm25 = 10n;
  const fraudScore = 2000n;
  const vspValue = 25000n;
  const wltcPhase = 3;
  const ts = BigInt(timestamp || 1700000100);
  const n = nonceFromSeed(nonceSeed);
  const sig = await signEmission(deviceSigner, registry, vehicleId, co2, co, nox, hc, pm25, ts, n);
  return registry
    .connect(stationSigner)
    .storeEmission(vehicleId, co2, co, nox, hc, pm25, fraudScore, vspValue, wltcPhase, ts, n, sig);
}

// ────────────────────────── fixtures ───────────────────────────────────

async function deployFixture() {
  const [admin, station, device, vehicleOwner, unauthorized] = await ethers.getSigners();

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

  // Wiring
  await (await registry.setTestingStation(station.address, true)).wait();
  await (await registry.setRegisteredDevice(device.address, true)).wait();
  await (await registry.setPUCCertificateContract(await puc.getAddress())).wait();
  await (await greenToken.setMinter(await puc.getAddress(), true)).wait();
  await (await puc.setAuthorizedIssuer(station.address, true)).wait();

  return { admin, station, device, vehicleOwner, unauthorized, registry, greenToken, puc };
}

// ════════════════════════════════════════════════════════════════════════
describe("Smart PUC — Full Test Suite (Hardhat)", function () {
  let admin, station, device, vehicleOwner, unauthorized;
  let registry, greenToken, puc;

  beforeEach(async () => {
    ({ admin, station, device, vehicleOwner, unauthorized, registry, greenToken, puc } = await deployFixture());
  });

  // ═══════════════════════════════════════════════════════════════════════
  describe("EmissionRegistry", () => {
    it("TC-01: Admin is deployer and thresholds are correct", async () => {
      expect(await registry.admin()).to.equal(admin.address);
      expect(await registry.BSVI_CO2()).to.equal(120000n);
      expect(await registry.BSVI_CO()).to.equal(1000n);
      expect(await registry.BSVI_NOX()).to.equal(60n);
      expect(await registry.BSVI_HC()).to.equal(100n);
      expect(await registry.BSVI_PM25()).to.equal(5n);
      expect(await registry.CES_PASS_CEILING()).to.equal(10000n);
      expect(await registry.FRAUD_ALERT_THRESHOLD()).to.equal(6500n);
    });

    it("TC-02: Store PASS record with valid device signature", async () => {
      const tx = await storePassRecord(registry, "MH12AB1234", station, device, 1);
      await expect(tx).to.emit(registry, "RecordStored");

      const record = await registry.getRecord("MH12AB1234", 0);
      expect(record.vehicleId).to.equal("MH12AB1234");
      expect(record.status).to.equal(true);
      expect(record.deviceAddress).to.equal(device.address);
      expect(record.stationAddress).to.equal(station.address);
    });

    it("TC-03: Store FAIL record emits ViolationDetected", async () => {
      await expect(storeFailRecord(registry, "MH12CD5678", station, device, 2))
        .to.emit(registry, "ViolationDetected");
      const record = await registry.getRecord("MH12CD5678", 0);
      expect(record.status).to.equal(false);
    });

    it("TC-04: Fraud score >= 6500 emits FraudDetected", async () => {
      const vid = "MH12EF9012";
      const co2 = 80000n, co = 500n, nox = 30n, hc = 50n, pm25 = 2n;
      const fraudScore = 7500n, vspValue = 12000n, wltcPhase = 1;
      const ts = 1700000200n;
      const n = nonceFromSeed(100);
      const sig = await signEmission(device, registry, vid, co2, co, nox, hc, pm25, ts, n);

      await expect(
        registry.connect(station).storeEmission(vid, co2, co, nox, hc, pm25, fraudScore, vspValue, wltcPhase, ts, n, sig)
      ).to.emit(registry, "FraudDetected");
    });

    it("TC-05: Multi-record stats aggregation", async () => {
      const vid = "MH12GH3456";
      await storePassRecord(registry, vid, station, device, 10, 1700000300);
      await storePassRecord(registry, vid, station, device, 11, 1700000400);
      await storeFailRecord(registry, vid, station, device, 12, 1700000500);

      const stats = await registry.getVehicleStats(vid);
      expect(stats.totalRecords).to.equal(3n);
      expect(stats.violations).to.equal(1n);
      expect(stats.averageCES).to.be.gt(0n);
    });

    it("TC-06: getViolations returns only FAIL records", async () => {
      const vid = "MH12IJ7890";
      await storePassRecord(registry, vid, station, device, 20, 1700000600);
      await storeFailRecord(registry, vid, station, device, 21, 1700000700);
      await storePassRecord(registry, vid, station, device, 22, 1700000800);

      const violations = await registry.getViolations(vid);
      expect(violations.length).to.equal(1);
      expect(violations[0].status).to.equal(false);
    });

    it("TC-07: Unauthorized station cannot store emissions", async () => {
      const n = nonceFromSeed(30);
      const sig = await signEmission(device, registry, "MH12XX0000", 80000n, 500n, 30n, 50n, 2n, 1700001000n, n);
      await expect(
        registry.connect(unauthorized).storeEmission(
          "MH12XX0000", 80000n, 500n, 30n, 50n, 2n, 1000n, 15000n, 2, 1700001000n, n, sig
        )
      ).to.be.revertedWith("Caller is not an authorized testing station");
    });

    it("TC-08: Auto-registration of vehicles", async () => {
      const vid = "MH12KL2345";
      const before = await registry.vehicleCount();
      await storePassRecord(registry, vid, station, device, 40, 1700000800);
      const after = await registry.vehicleCount();
      expect(after).to.equal(before + 1n);
      const list = await registry.getRegisteredVehicles();
      expect(list).to.include(vid);
    });

    it("TC-09: Empty vehicle ID rejected", async () => {
      const n = nonceFromSeed(50);
      const sig = await signEmission(device, registry, "", 80000n, 500n, 30n, 50n, 2n, 1700000900n, n);
      await expect(
        registry.connect(station).storeEmission(
          "", 80000n, 500n, 30n, 50n, 2n, 1000n, 15000n, 2, 1700000900n, n, sig
        )
      ).to.be.revertedWith("Vehicle ID cannot be empty");
    });

    it("TC-10: All record fields stored correctly", async () => {
      const vid = "MH12MN6789";
      const co2 = 105000n, co = 600n, nox = 35n, hc = 55n, pm25 = 3n;
      const fraudScore = 1500n, vspValue = 16000n, wltcPhase = 2;
      const ts = 1700001000n;
      const n = nonceFromSeed(60);
      const sig = await signEmission(device, registry, vid, co2, co, nox, hc, pm25, ts, n);

      await registry.connect(station).storeEmission(
        vid, co2, co, nox, hc, pm25, fraudScore, vspValue, wltcPhase, ts, n, sig
      );

      const r = await registry.getRecord(vid, 0);
      expect(r.vehicleId).to.equal(vid);
      expect(r.co2).to.equal(co2);
      expect(r.co).to.equal(co);
      expect(r.nox).to.equal(nox);
      expect(r.hc).to.equal(hc);
      expect(r.pm25).to.equal(pm25);
      expect(r.fraudScore).to.equal(fraudScore);
      expect(r.vspValue).to.equal(vspValue);
      expect(r.wltcPhase).to.equal(BigInt(wltcPhase));
      expect(r.timestamp).to.equal(ts);
      expect(r.deviceAddress).to.equal(device.address);
      expect(r.stationAddress).to.equal(station.address);
      expect(r.cesScore).to.be.gt(0n);
    });

    it("TC-11: On-chain CES computation matches expected", async () => {
      expect(await registry.computeCES(120000, 1000, 60, 100, 5)).to.equal(10000n);
      const half = await registry.computeCES(60000, 500, 30, 50, 2);
      expect(half).to.be.gte(4800n);
      expect(half).to.be.lte(5200n);
      expect(await registry.computeCES(0, 0, 0, 0, 0)).to.equal(0n);
    });

    it("TC-12: Nonce replay protection", async () => {
      const vid = "MH12RP0001";
      const n = nonceFromSeed(70);
      const ts = 1700002000n;
      const sig = await signEmission(device, registry, vid, 80000n, 500n, 30n, 50n, 2n, ts, n);
      await registry.connect(station).storeEmission(vid, 80000n, 500n, 30n, 50n, 2n, 1000n, 15000n, 2, ts, n, sig);
      await expect(
        registry.connect(station).storeEmission(vid, 80000n, 500n, 30n, 50n, 2n, 1000n, 15000n, 2, ts, n, sig)
      ).to.be.revertedWith("Nonce already used");
    });

    it("TC-13: Invalid device signature rejected", async () => {
      const vid = "MH12BADSIG";
      const n = nonceFromSeed(80);
      const ts = 1700003000n;
      // Signed by unauthorized account (not a registered device)
      const sig = await signEmission(unauthorized, registry, vid, 80000n, 500n, 30n, 50n, 2n, ts, n);
      await expect(
        registry.connect(station).storeEmission(vid, 80000n, 500n, 30n, 50n, 2n, 1000n, 15000n, 2, ts, n, sig)
      ).to.be.revertedWith("Signature from unregistered device");
    });

    it("TC-14: Consecutive pass tracking", async () => {
      const vid = "MH12PASS01";
      await storePassRecord(registry, vid, station, device, 90, 1700004000);
      expect(await registry.consecutivePassCount(vid)).to.equal(1n);
      await storePassRecord(registry, vid, station, device, 91, 1700004100);
      expect(await registry.consecutivePassCount(vid)).to.equal(2n);
      await storeFailRecord(registry, vid, station, device, 92, 1700004200);
      expect(await registry.consecutivePassCount(vid)).to.equal(0n);
      await storePassRecord(registry, vid, station, device, 93, 1700004300);
      expect(await registry.consecutivePassCount(vid)).to.equal(1n);
    });

    it("TC-15: Certificate eligibility after 3 consecutive passes", async () => {
      const vid = "MH12ELIG01";
      await storePassRecord(registry, vid, station, device, 200, 1700005000);
      await storePassRecord(registry, vid, station, device, 201, 1700005100);
      let r = await registry.isCertificateEligible(vid);
      expect(r.eligible).to.equal(false);
      await expect(storePassRecord(registry, vid, station, device, 202, 1700005200))
        .to.emit(registry, "CertificateEligible");
      r = await registry.isCertificateEligible(vid);
      expect(r.eligible).to.equal(true);
      expect(r.passes).to.equal(3n);
    });

    it("TC-16: Soft vehicle cap (disabled by default, settable)", async () => {
      await storePassRecord(registry, "VEH_A", station, device, 300, 1700006000);
      await storePassRecord(registry, "VEH_B", station, device, 301, 1700006100);
      await storePassRecord(registry, "VEH_C", station, device, 302, 1700006200);
      expect(await registry.vehicleCount()).to.equal(3n);

      // Same vehicle again — count does not increase
      await storePassRecord(registry, "VEH_A", station, device, 303, 1700006300);
      expect(await registry.vehicleCount()).to.equal(3n);

      expect(await registry.softVehicleCap()).to.equal(0n);
      await registry.setSoftVehicleCap(3);
      await expect(storePassRecord(registry, "VEH_D", station, device, 304, 1700006400))
        .to.be.revertedWith("Soft vehicle cap reached");
      await registry.setSoftVehicleCap(0);
    });

    it("TC-17: Pagination works correctly", async () => {
      const vid = "MH12PAGE01";
      for (let i = 0; i < 5; i++) {
        await storePassRecord(registry, vid, station, device, 400 + i, 1700007000 + i * 100);
      }
      expect((await registry.getRecordsPaginated(vid, 0, 2)).length).to.equal(2);
      expect((await registry.getRecordsPaginated(vid, 2, 2)).length).to.equal(2);
      expect((await registry.getRecordsPaginated(vid, 4, 2)).length).to.equal(1);
      expect((await registry.getRecordsPaginated(vid, 10, 2)).length).to.equal(0);
    });
  });

  // ═══════════════════════════════════════════════════════════════════════
  describe("PUCCertificate", () => {
    async function makeEligible(vid, nonceSeedStart) {
      for (let i = 0; i < 3; i++) {
        await storePassRecord(registry, vid, station, device, nonceSeedStart + i, 1700010000 + i * 100);
      }
    }

    it("TC-18: Certificate issuance after 3 consecutive passes", async () => {
      const vid = "MH12CERT01";
      await makeEligible(vid, 500);
      const tx = await puc
        .connect(station)
        ["issueCertificate(string,address,string)"](vid, vehicleOwner.address, "");
      const receipt = await tx.wait();
      const event = receipt.logs.map((l) => {
        try { return puc.interface.parseLog(l); } catch { return null; }
      }).find((e) => e && e.name === "CertificateIssued");
      expect(event).to.exist;
      const tokenId = event.args.tokenId;
      expect(tokenId).to.be.gt(0n);
      expect(await puc.ownerOf(tokenId)).to.equal(vehicleOwner.address);
      const cert = await puc.getCertificate(tokenId);
      expect(cert.vehicleId).to.equal(vid);
      expect(cert.revoked).to.equal(false);
      expect(cert.averageCES).to.be.gt(0n);
    });

    it("TC-19: Rejected if insufficient passes", async () => {
      const vid = "MH12NOPASS";
      await storePassRecord(registry, vid, station, device, 510, 1700011000);
      await storePassRecord(registry, vid, station, device, 511, 1700011100);
      await expect(
        puc.connect(station)["issueCertificate(string,address,string)"](vid, vehicleOwner.address, "")
      ).to.be.revertedWith("Insufficient consecutive passes");
    });

    it("TC-20: Rejected if CES too high (all FAIL records)", async () => {
      const vid = "MH12HICES";
      await storeFailRecord(registry, vid, station, device, 520, 1700012000);
      await storeFailRecord(registry, vid, station, device, 521, 1700012100);
      await storeFailRecord(registry, vid, station, device, 522, 1700012200);
      await expect(
        puc.connect(station)["issueCertificate(string,address,string)"](vid, vehicleOwner.address, "")
      ).to.be.reverted;
    });

    it("TC-21: Revocation by authority", async () => {
      const vid = "MH12REVOKE";
      await makeEligible(vid, 530);
      const tx = await puc
        .connect(station)
        ["issueCertificate(string,address,string)"](vid, vehicleOwner.address, "");
      const rcpt = await tx.wait();
      const issued = rcpt.logs.map((l) => {
        try { return puc.interface.parseLog(l); } catch { return null; }
      }).find((e) => e && e.name === "CertificateIssued");
      const tokenId = issued.args.tokenId;
      await expect(puc.revokeCertificate(tokenId, "Failed spot check"))
        .to.emit(puc, "CertificateRevoked");
      const cert = await puc.getCertificate(tokenId);
      expect(cert.revoked).to.equal(true);
      const v = await puc.isValid(vid);
      expect(v.valid).to.equal(false);
    });

    it("TC-22: Duplicate certificate rejected while first is valid", async () => {
      const vid = "MH12DUP01";
      await makeEligible(vid, 540);
      await puc.connect(station)["issueCertificate(string,address,string)"](vid, vehicleOwner.address, "");
      await expect(
        puc.connect(station)["issueCertificate(string,address,string)"](vid, vehicleOwner.address, "")
      ).to.be.revertedWith("Vehicle already has a valid certificate");
    });

    it("TC-23: TokenURI set, retrieved, and base-URI concatenated", async () => {
      const vid = "MH12URI01";
      await makeEligible(vid, 550);
      const tx = await puc
        .connect(station)
        ["issueCertificate(string,address,string)"](vid, vehicleOwner.address, "QmTestHash123");
      const rcpt = await tx.wait();
      const issued = rcpt.logs.map((l) => {
        try { return puc.interface.parseLog(l); } catch { return null; }
      }).find((e) => e && e.name === "CertificateIssued");
      const tokenId = issued.args.tokenId;

      expect(await puc.tokenURI(tokenId)).to.equal("QmTestHash123");
      await puc.setBaseURI("https://ipfs.io/ipfs/");
      expect(await puc.tokenURI(tokenId)).to.equal("https://ipfs.io/ipfs/QmTestHash123");
      await puc.setTokenURI(tokenId, "QmUpdatedHash456");
      expect(await puc.tokenURI(tokenId)).to.equal("https://ipfs.io/ipfs/QmUpdatedHash456");
    });

    it("TC-24: Proportional GreenToken reward on certificate issuance", async () => {
      const vid = "MH12GREEN1";
      await makeEligible(vid, 560);
      const before = await greenToken.balanceOf(vehicleOwner.address);
      const tx = await puc
        .connect(station)
        ["issueCertificate(string,address,string)"](vid, vehicleOwner.address, "");
      const rcpt = await tx.wait();
      const after = await greenToken.balanceOf(vehicleOwner.address);
      const reward = after - before;

      const min = ethers.parseEther("50");
      const max = ethers.parseEther("200");
      expect(reward).to.be.gte(min);
      expect(reward).to.be.lte(max);

      const issued = rcpt.logs.map((l) => {
        try { return puc.interface.parseLog(l); } catch { return null; }
      }).find((e) => e && e.name === "CertificateIssued");
      const tokenId = issued.args.tokenId;
      const cert = await puc.getCertificate(tokenId);
      const expected = await puc.computeRewardAmount(cert.averageCES);
      expect(reward).to.equal(expected);
    });

    it("TC-25: Non-authority cannot issue certificates", async () => {
      const vid = "MH12NOAUTH";
      await storePassRecord(registry, vid, station, device, 570, 1700013000);
      await storePassRecord(registry, vid, station, device, 571, 1700013100);
      await storePassRecord(registry, vid, station, device, 572, 1700013200);
      await expect(
        puc.connect(unauthorized)["issueCertificate(string,address,string)"](vid, vehicleOwner.address, "")
      ).to.be.revertedWith("Not authorized to issue certificates");
    });
  });

  // ═══════════════════════════════════════════════════════════════════════
  describe("GreenToken", () => {
    it("TC-26: Only authorized minters can mint", async () => {
      await expect(
        greenToken.connect(unauthorized).mint(vehicleOwner.address, ethers.parseEther("100"))
      ).to.be.revertedWith("Not authorized to mint");

      await greenToken.setMinter(station.address, true);
      await greenToken.connect(station).mint(vehicleOwner.address, ethers.parseEther("50"));
      expect(await greenToken.balanceOf(vehicleOwner.address)).to.equal(ethers.parseEther("50"));
    });

    it("TC-27: Token balance and reward tracking", async () => {
      await greenToken.setMinter(station.address, true);
      await greenToken.connect(station).mint(vehicleOwner.address, ethers.parseEther("100"));
      await greenToken.connect(station).mint(vehicleOwner.address, ethers.parseEther("50"));
      const summary = await greenToken.getRewardSummary(vehicleOwner.address);
      expect(summary.balance).to.equal(ethers.parseEther("150"));
      expect(summary.earned).to.equal(ethers.parseEther("150"));
      expect(await greenToken.totalRewardsMinted()).to.equal(ethers.parseEther("150"));
    });

    it("TC-28: Redeem burns tokens", async () => {
      await greenToken.setMinter(station.address, true);
      await greenToken.connect(station).mint(vehicleOwner.address, ethers.parseEther("100"));
      await expect(greenToken.connect(vehicleOwner).redeem(0)).to.emit(greenToken, "Redeemed");
      expect(await greenToken.balanceOf(vehicleOwner.address)).to.equal(ethers.parseEther("50"));
      expect(await greenToken.totalRedeemed()).to.equal(ethers.parseEther("50"));
    });

    it("TC-29: Insufficient balance for redemption rejected", async () => {
      await expect(greenToken.connect(vehicleOwner).redeem(0))
        .to.be.revertedWith("Insufficient GCT balance");
    });

    it("TC-30: Redemption stats tracked correctly", async () => {
      await greenToken.setMinter(station.address, true);
      await greenToken.connect(station).mint(vehicleOwner.address, ethers.parseEther("200"));
      await greenToken.connect(vehicleOwner).redeem(0);
      await greenToken.connect(vehicleOwner).redeem(1);
      await greenToken.connect(vehicleOwner).redeem(3);
      const stats = await greenToken.getRedemptionStats(vehicleOwner.address);
      expect(stats.totalCount).to.equal(3n);
      expect(stats.tollDiscounts).to.equal(1n);
      expect(stats.parkingWaivers).to.equal(1n);
      expect(stats.taxCredits).to.equal(0n);
      expect(stats.priorityServices).to.equal(1n);
      expect(await greenToken.balanceOf(vehicleOwner.address)).to.equal(ethers.parseEther("100"));
      expect(await greenToken.nextRedemptionId()).to.equal(3n);
    });
  });

  // ═══════════════════════════════════════════════════════════════════════
  // Proxy / upgrade semantics
  // ═══════════════════════════════════════════════════════════════════════
  describe("UUPS proxy semantics", () => {
    it("TC-31: State survives a no-op upgrade of EmissionRegistry", async () => {
      await storePassRecord(registry, "UPGRADE01", station, device, 700, 1700020000);
      const before = await registry.vehicleCount();

      // Re-upgrade to the same implementation — state must be preserved.
      const EmissionRegistry = await ethers.getContractFactory("EmissionRegistry", admin);
      const upgraded = await upgrades.upgradeProxy(await registry.getAddress(), EmissionRegistry);
      await upgraded.waitForDeployment();

      const after = await upgraded.vehicleCount();
      expect(after).to.equal(before);
      expect(await upgraded.admin()).to.equal(admin.address);
    });

    it("TC-32: Only admin can authorize an upgrade", async () => {
      const EmissionRegistry = await ethers.getContractFactory("EmissionRegistry", unauthorized);
      await expect(
        upgrades.upgradeProxy(await registry.getAddress(), EmissionRegistry)
      ).to.be.reverted;
    });

    it("TC-33: GreenToken and PUCCertificate also upgrade cleanly", async () => {
      const GT = await ethers.getContractFactory("GreenToken", admin);
      const gt2 = await upgrades.upgradeProxy(await greenToken.getAddress(), GT);
      await gt2.waitForDeployment();
      expect(await gt2.name()).to.equal("Green Credit Token");

      const PUC = await ethers.getContractFactory("PUCCertificate", admin);
      const puc2 = await upgrades.upgradeProxy(await puc.getAddress(), PUC);
      await puc2.waitForDeployment();
      expect(await puc2.authority()).to.equal(admin.address);
    });
  });

  // ═══════════════════════════════════════════════════════════════════════
  // v3.2 additions (EIP-712, Pausable, BS-IV, claimVehicle, phase summary,
  // Merkle batch commit, concave reward curve)
  // ═══════════════════════════════════════════════════════════════════════
  describe("v3.2 EIP-712 signatures", () => {
    it("TC-34: Device signature is rejected when signed under the wrong contract (domain binding)", async () => {
      // Deploy a sibling registry so signatures under the original domain
      // must not be valid under the new domain.
      const EmissionRegistry = await ethers.getContractFactory("EmissionRegistry", admin);
      const sibling = await upgrades.deployProxy(EmissionRegistry, [], { kind: "uups", initializer: "initialize" });
      await sibling.waitForDeployment();
      await (await sibling.setTestingStation(station.address, true)).wait();
      await (await sibling.setRegisteredDevice(device.address, true)).wait();

      const vid = "MH12DOMAIN";
      const ts = 1700040000n;
      const n = nonceFromSeed(800);
      // Sign for the ORIGINAL registry, try to submit to the SIBLING.
      const sig = await signEmission(device, registry, vid, 80000n, 500n, 30n, 50n, 2n, ts, n);
      await expect(
        sibling.connect(station).storeEmission(vid, 80000n, 500n, 30n, 50n, 2n, 1000n, 15000n, 2, ts, n, sig)
      ).to.be.revertedWith("Signature from unregistered device");
    });

    it("TC-35: getEmissionDigest matches off-chain EIP-712 digest", async () => {
      const vid = "MH12DIG01";
      const ts = 1700041000n;
      const n = nonceFromSeed(810);
      const sig = await signEmission(device, registry, vid, 80000n, 500n, 30n, 50n, 2n, ts, n);

      // A successful storeEmission implies the contract's recovered signer
      // matches the device — this indirectly verifies the digest.
      await registry.connect(station).storeEmission(vid, 80000n, 500n, 30n, 50n, 2n, 1000n, 15000n, 2, ts, n, sig);
      const r = await registry.getRecord(vid, 0);
      expect(r.deviceAddress).to.equal(device.address);
    });
  });

  describe("v3.2 Pausable circuit breaker", () => {
    it("TC-36: Admin can pause storeEmission and unpause it", async () => {
      await registry.pause();
      const n = nonceFromSeed(820);
      const sig = await signEmission(device, registry, "PAUSE01", 80000n, 500n, 30n, 50n, 2n, 1700050000n, n);
      await expect(
        registry.connect(station).storeEmission("PAUSE01", 80000n, 500n, 30n, 50n, 2n, 1000n, 15000n, 2, 1700050000n, n, sig)
      ).to.be.revertedWith("Pausable: paused");

      await registry.unpause();
      await expect(
        registry.connect(station).storeEmission("PAUSE01", 80000n, 500n, 30n, 50n, 2n, 1000n, 15000n, 2, 1700050000n, n, sig)
      ).to.emit(registry, "RecordStored");
    });

    it("TC-37: Non-admin cannot pause", async () => {
      await expect(registry.connect(unauthorized).pause())
        .to.be.revertedWith("Only admin can call this function");
    });

    it("TC-38: GreenToken and PUCCertificate also pausable", async () => {
      await greenToken.pause();
      await expect(greenToken.connect(vehicleOwner).redeem(0))
        .to.be.revertedWith("Pausable: paused");
      await greenToken.unpause();

      await puc.pause();
      await storePassRecord(registry, "PAUSECERT", station, device, 830, 1700051000);
      await storePassRecord(registry, "PAUSECERT", station, device, 831, 1700051100);
      await storePassRecord(registry, "PAUSECERT", station, device, 832, 1700051200);
      await expect(
        puc.connect(station)["issueCertificate(string,address,string)"]("PAUSECERT", vehicleOwner.address, "")
      ).to.be.revertedWith("Pausable: paused");
      await puc.unpause();
    });
  });

  describe("v3.2 BS-IV support", () => {
    it("TC-39: Vehicle tagged BS-IV uses the looser BS-IV thresholds", async () => {
      const vid = "BS4VEHICLE";
      // A BS-IV-tagged vehicle passing with CO=1500 (x1000) which would FAIL
      // under BS-VI's 1000 cap but is below BS-IV's 2300 cap.
      await registry.setVehicleStandard(vid, 1); // 1 = BS4
      const co2 = 100000n, co = 1500n, nox = 80n, hc = 120n, pm25 = 4n;
      const ts = 1700060000n;
      const n = nonceFromSeed(900);
      const sig = await signEmission(device, registry, vid, co2, co, nox, hc, pm25, ts, n);
      await registry.connect(station).storeEmission(vid, co2, co, nox, hc, pm25, 500n, 12000n, 2, ts, n, sig);
      const r = await registry.getRecord(vid, 0);
      // CES should be computed against BS-IV thresholds and still pass.
      expect(r.status).to.equal(true);
    });

    it("TC-40: computeCESForStandard(BS4) differs from computeCES(BS6)", async () => {
      const ces6 = await registry.computeCES(100000, 1500, 80, 120, 4);
      const ces4 = await registry.computeCESForStandard(100000, 1500, 80, 120, 4, 1);
      expect(ces6).to.be.gt(ces4); // BS-VI is strict → higher CES for same inputs
    });

    it("TC-41: Default standard for unset vehicle is BS6", async () => {
      const vid = "DEFAULTSTD";
      expect(await registry.vehicleStandard(vid)).to.equal(0n); // BS6 = 0
    });
  });

  describe("v3.2 Hardened claimVehicle", () => {
    it("TC-42: claimVehicle requires a valid admin EIP-712 signature", async () => {
      const vid = "CLAIMTEST1";
      const adminSig = await signVehicleClaim(admin, registry, vid, vehicleOwner.address);
      await expect(
        registry.connect(vehicleOwner).claimVehicle(vid, adminSig)
      ).to.emit(registry, "VehicleOwnerSet");
      expect(await registry.vehicleOwners(vid)).to.equal(vehicleOwner.address);
    });

    it("TC-43: claimVehicle rejects signature from non-admin", async () => {
      const vid = "CLAIMTEST2";
      const badSig = await signVehicleClaim(unauthorized, registry, vid, vehicleOwner.address);
      await expect(
        registry.connect(vehicleOwner).claimVehicle(vid, badSig)
      ).to.be.revertedWith("Invalid admin signature");
    });

    it("TC-44: claimVehicle rejects signature for wrong claimant (no squatting)", async () => {
      const vid = "CLAIMTEST3";
      // Admin signed for `vehicleOwner`, but `unauthorized` tries to claim.
      const adminSig = await signVehicleClaim(admin, registry, vid, vehicleOwner.address);
      await expect(
        registry.connect(unauthorized).claimVehicle(vid, adminSig)
      ).to.be.revertedWith("Invalid admin signature");
    });

    it("TC-45: claimVehicle rejects already-claimed vehicle", async () => {
      const vid = "CLAIMTEST4";
      const adminSig = await signVehicleClaim(admin, registry, vid, vehicleOwner.address);
      await registry.connect(vehicleOwner).claimVehicle(vid, adminSig);
      await expect(
        registry.connect(vehicleOwner).claimVehicle(vid, adminSig)
      ).to.be.revertedWith("Vehicle already claimed");
    });
  });

  describe("v3.2 Per-phase WLTC summary", () => {
    it("TC-46: reportPhaseSummary emits PhaseCompleted", async () => {
      await expect(
        registry.connect(station).reportPhaseSummary("PHASEVEH", 2, 6500, 8400, 1700070000)
      ).to.emit(registry, "PhaseCompleted");
    });

    it("TC-47: reportPhaseSummary rejects invalid phase > 3", async () => {
      await expect(
        registry.connect(station).reportPhaseSummary("PHASEVEH", 5, 6500, 8400, 1700070100)
      ).to.be.revertedWith("Invalid WLTC phase (0-3)");
    });

    it("TC-48: Only authorized station can report phase summary", async () => {
      await expect(
        registry.connect(unauthorized).reportPhaseSummary("PHASEVEH", 1, 5000, 4000, 1700070200)
      ).to.be.revertedWith("Caller is not an authorized testing station");
    });
  });

  describe("v3.2 Merkle batch commit", () => {
    it("TC-49: commitBatchRoot stores a root and emits an event", async () => {
      const root = ethers.keccak256(ethers.toUtf8Bytes("batch-42"));
      await expect(
        registry.connect(station).commitBatchRoot("BATCHVEH", 42, root, 100)
      ).to.emit(registry, "BatchRootCommitted");

      const [storedRoot, storedCount] = await registry.getBatchRoot("BATCHVEH", 42);
      expect(storedRoot).to.equal(root);
      expect(storedCount).to.equal(100n);
    });

    it("TC-50: Duplicate commitBatchRoot for same (vehicle, day) is rejected", async () => {
      const root = ethers.keccak256(ethers.toUtf8Bytes("batch-dup"));
      await registry.connect(station).commitBatchRoot("DUPBATCH", 1, root, 50);
      await expect(
        registry.connect(station).commitBatchRoot("DUPBATCH", 1, root, 50)
      ).to.be.revertedWith("Batch already committed");
    });

    it("TC-51: commitBatchRoot requires an authorized station", async () => {
      const root = ethers.keccak256(ethers.toUtf8Bytes("batch-unauth"));
      await expect(
        registry.connect(unauthorized).commitBatchRoot("UNAUTHBATCH", 1, root, 50)
      ).to.be.revertedWith("Caller is not an authorized testing station");
    });
  });

  describe("v3.2 Concave reward curve", () => {
    it("TC-52: computeRewardAmount(0) returns MAX (200 GCT)", async () => {
      expect(await puc.computeRewardAmount(0))
        .to.equal(ethers.parseEther("200"));
    });

    it("TC-53: computeRewardAmount(CEILING) returns MIN (50 GCT)", async () => {
      expect(await puc.computeRewardAmount(10000))
        .to.equal(ethers.parseEther("50"));
    });

    it("TC-54: Concave curve is below linear midpoint at CES=5000", async () => {
      // Linear midpoint would be 125 GCT; concave midpoint is
      // 50 + 150 * (5000*5000/10000) / 10000 = 50 + 150*2500/10000 = 87.5 GCT
      const mid = await puc.computeRewardAmount(5000);
      expect(mid).to.be.lt(ethers.parseEther("125"));
      expect(mid).to.be.gte(ethers.parseEther("80"));
      expect(mid).to.be.lte(ethers.parseEther("95"));
    });

    it("TC-55: Concave curve is strictly decreasing", async () => {
      const r0 = await puc.computeRewardAmount(0);
      const r1 = await puc.computeRewardAmount(2500);
      const r2 = await puc.computeRewardAmount(5000);
      const r3 = await puc.computeRewardAmount(7500);
      const r4 = await puc.computeRewardAmount(10000);
      expect(r0).to.be.gt(r1);
      expect(r1).to.be.gt(r2);
      expect(r2).to.be.gt(r3);
      expect(r3).to.be.gt(r4);
    });
  });

  describe("v3.2 First-PUC validity branch (CMVR Rule 115)", () => {
    async function makeEligible(vid, nonceSeedStart) {
      for (let i = 0; i < 3; i++) {
        await storePassRecord(registry, vid, station, device, nonceSeedStart + i, 1700020000 + i * 100);
      }
    }

    it("TC-56: First-ever PUC auto-detects FIRST flag and gets 360-day validity", async () => {
      const vid = "MH12FIRST1";
      await makeEligible(vid, 800);
      const tx = await puc
        .connect(station)
        ["issueCertificate(string,address,string)"](vid, vehicleOwner.address, "");
      const rcpt = await tx.wait();
      const issued = rcpt.logs.map((l) => {
        try { return puc.interface.parseLog(l); } catch { return null; }
      }).find((e) => e && e.name === "CertificateIssued");
      const tokenId = issued.args.tokenId;
      const cert = await puc.getCertificate(tokenId);
      const duration = BigInt(cert.expiryTimestamp) - BigInt(cert.issueTimestamp);
      // 360 days in seconds
      expect(duration).to.equal(360n * 24n * 60n * 60n);
      expect(cert.isFirstPUC).to.equal(true);
    });

    it("TC-57: Renewal after revocation auto-detects NOT-FIRST and gets 180-day validity", async () => {
      const vid = "MH12FIRST2";
      await makeEligible(vid, 810);
      // First PUC
      const tx1 = await puc
        .connect(station)
        ["issueCertificate(string,address,string)"](vid, vehicleOwner.address, "");
      const rcpt1 = await tx1.wait();
      const issued1 = rcpt1.logs.map((l) => {
        try { return puc.interface.parseLog(l); } catch { return null; }
      }).find((e) => e && e.name === "CertificateIssued");
      const tokenId1 = issued1.args.tokenId;
      // Revoke so a second PUC can be issued
      await puc.revokeCertificate(tokenId1, "Unit-test revocation");
      // Second PUC
      const tx2 = await puc
        .connect(station)
        ["issueCertificate(string,address,string)"](vid, vehicleOwner.address, "");
      const rcpt2 = await tx2.wait();
      const issued2 = rcpt2.logs.map((l) => {
        try { return puc.interface.parseLog(l); } catch { return null; }
      }).find((e) => e && e.name === "CertificateIssued");
      const tokenId2 = issued2.args.tokenId;
      const cert2 = await puc.getCertificate(tokenId2);
      const duration = BigInt(cert2.expiryTimestamp) - BigInt(cert2.issueTimestamp);
      // 180 days — not first
      expect(duration).to.equal(180n * 24n * 60n * 60n);
      expect(cert2.isFirstPUC).to.equal(false);
    });

    it("TC-58: Explicit overload issueCertificateWithFirstFlag honours the flag", async () => {
      const vid = "MH12FIRST3";
      await makeEligible(vid, 820);
      // Force NOT-first on a vehicle with no prior certificate — should get 180 days.
      const tx = await puc
        .connect(station)
        .issueCertificateWithFirstFlag(vid, vehicleOwner.address, "", false);
      const rcpt = await tx.wait();
      const issued = rcpt.logs.map((l) => {
        try { return puc.interface.parseLog(l); } catch { return null; }
      }).find((e) => e && e.name === "CertificateIssued");
      const tokenId = issued.args.tokenId;
      const cert = await puc.getCertificate(tokenId);
      const duration = BigInt(cert.expiryTimestamp) - BigInt(cert.issueTimestamp);
      expect(duration).to.equal(180n * 24n * 60n * 60n);
      expect(cert.isFirstPUC).to.equal(false);
    });
  });

  describe("v3.2.1 Per-vehicle rate limit (audit G8)", () => {
    it("TC-59: Default rate limit is 0 (disabled) for backward compatibility", async () => {
      expect(await registry.perVehicleRateLimitSeconds()).to.equal(0n);
    });

    it("TC-60: setPerVehicleRateLimit requires admin", async () => {
      await expect(registry.connect(unauthorized).setPerVehicleRateLimit(3))
        .to.be.revertedWith("Only admin can call this function");
    });

    it("TC-61: Enabled rate limit rejects back-to-back writes to the same vehicle", async () => {
      // Snapshot-style: enable, write once, attempt an immediate second
      // write in the same block → should revert. Then advance time and
      // verify the second write succeeds.
      await registry.connect(admin).setPerVehicleRateLimit(10); // 10s gap
      const vid = "MH12RATELIM";
      // First write — should succeed.
      await storePassRecord(registry, vid, station, device, 900, 1_700_050_000);
      // Second write — same vehicle, <10s later → revert.
      await expect(
        storePassRecord(registry, vid, station, device, 901, 1_700_050_001)
      ).to.be.revertedWith("Per-vehicle rate limit: writes too frequent");
      // Advance chain time by 11 seconds and retry — should succeed.
      await ethers.provider.send("evm_increaseTime", [11]);
      await ethers.provider.send("evm_mine", []);
      await storePassRecord(registry, vid, station, device, 902, 1_700_050_012);
      // Reset to 0 so later tests are unaffected.
      await registry.connect(admin).setPerVehicleRateLimit(0);
    });
  });

  describe("v3.2.2 Privacy mode (audit L11 / G6)", () => {
    it("TC-62: privacyMode defaults to false and computeVehicleIdHash is deterministic", async () => {
      expect(await registry.privacyMode()).to.equal(false);
      const h1 = await registry.computeVehicleIdHash("MH12AB1234");
      const h2 = await registry.computeVehicleIdHash("MH12AB1234");
      expect(h1).to.equal(h2);
      // Hash matches keccak256(bytes("MH12AB1234"))
      const expected = ethers.keccak256(ethers.toUtf8Bytes("MH12AB1234"));
      expect(h1).to.equal(expected);
    });

    it("TC-63: setPrivacyMode requires admin and emits PrivacyModeSet", async () => {
      await expect(registry.connect(unauthorized).setPrivacyMode(true))
        .to.be.revertedWith("Only admin can call this function");
      await expect(registry.connect(admin).setPrivacyMode(true))
        .to.emit(registry, "PrivacyModeSet")
        .withArgs(true);
      expect(await registry.privacyMode()).to.equal(true);
      // Reset so later tests see the default.
      await registry.connect(admin).setPrivacyMode(false);
    });

    it("TC-64: storeEmission emits EmissionStoredHashed only when privacy mode is on", async () => {
      const vid = "MH12PRIV01";
      // Privacy OFF (default): no hashed event.
      const txOff = await storePassRecord(registry, vid, station, device, 1_000, 1_700_100_000);
      const rcOff = await txOff.wait();
      const hashedOff = rcOff.logs.map((l) => {
        try { return registry.interface.parseLog(l); } catch { return null; }
      }).filter((e) => e && e.name === "EmissionStoredHashed");
      expect(hashedOff.length).to.equal(0);

      // Privacy ON: hashed event IS emitted with the correct topic.
      await registry.connect(admin).setPrivacyMode(true);
      const vid2 = "MH12PRIV02";
      const txOn = await storePassRecord(registry, vid2, station, device, 1_001, 1_700_100_100);
      const rcOn = await txOn.wait();
      const hashedOn = rcOn.logs.map((l) => {
        try { return registry.interface.parseLog(l); } catch { return null; }
      }).filter((e) => e && e.name === "EmissionStoredHashed");
      expect(hashedOn.length).to.equal(1);
      const expectedHash = ethers.keccak256(ethers.toUtf8Bytes(vid2));
      expect(hashedOn[0].args.vehicleIdHash).to.equal(expectedHash);
      expect(hashedOn[0].args.passed).to.equal(true);
      // Reset.
      await registry.connect(admin).setPrivacyMode(false);
    });
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// MultiSigAdmin (audit S5) — 2-of-3 governance of the EmissionRegistry admin
// ─────────────────────────────────────────────────────────────────────────────

describe("MultiSigAdmin (audit S5)", () => {
  let admin, sig1, sig2, sig3, outsider;
  let registry, multisig;

  beforeEach(async () => {
    [admin, sig1, sig2, sig3, outsider] = await ethers.getSigners();

    // Deploy a fresh EmissionRegistry proxy for this isolated suite.
    const ER = await ethers.getContractFactory("EmissionRegistry", admin);
    registry = await upgrades.deployProxy(ER, [], { kind: "uups", initializer: "initialize" });
    await registry.waitForDeployment();

    // Deploy the 2-of-3 multisig with (sig1, sig2, sig3) as signers.
    const MS = await ethers.getContractFactory("MultiSigAdmin", admin);
    multisig = await MS.deploy([sig1.address, sig2.address, sig3.address], 2);
    await multisig.waitForDeployment();

    // Hand the EmissionRegistry admin role to the multisig.
    await registry.connect(admin).transferAdmin(await multisig.getAddress());
    expect(await registry.admin()).to.equal(await multisig.getAddress());
  });

  it("TC-65: constructor rejects zero signer, duplicate signer, and bad threshold", async () => {
    const MS = await ethers.getContractFactory("MultiSigAdmin", admin);
    await expect(MS.deploy([], 1)).to.be.reverted; // need at least 1 signer
    await expect(MS.deploy([sig1.address], 2))
      .to.be.revertedWith("MultiSigAdmin: invalid threshold");
    await expect(MS.deploy([sig1.address, sig1.address], 2))
      .to.be.revertedWith("MultiSigAdmin: duplicate signer");
    await expect(MS.deploy([ethers.ZeroAddress], 1))
      .to.be.revertedWith("MultiSigAdmin: zero signer");
  });

  it("TC-66: propose() by a non-signer reverts", async () => {
    const calldata = registry.interface.encodeFunctionData("setPerVehicleRateLimit", [5]);
    await expect(
      multisig.connect(outsider).propose(await registry.getAddress(), calldata, 0)
    ).to.be.revertedWithCustomError(multisig, "NotSigner");
  });

  it("TC-67: single confirmation is below threshold — execute reverts", async () => {
    const calldata = registry.interface.encodeFunctionData("setPerVehicleRateLimit", [5]);
    const tx = await multisig.connect(sig1).propose(await registry.getAddress(), calldata, 0);
    await tx.wait();
    // Proposer auto-confirms (count = 1), threshold = 2.
    await expect(multisig.connect(sig1).execute(0))
      .to.be.revertedWithCustomError(multisig, "BelowThreshold");
    expect(await registry.perVehicleRateLimitSeconds()).to.equal(0n);
  });

  it("TC-68: 2-of-3 confirmation executes the admin call end-to-end", async () => {
    const calldata = registry.interface.encodeFunctionData("setPerVehicleRateLimit", [7]);
    await multisig.connect(sig1).propose(await registry.getAddress(), calldata, 0);
    await multisig.connect(sig2).confirm(0); // 2nd confirmation reaches threshold
    const ex = await multisig.connect(sig3).execute(0); // any signer may execute
    await ex.wait();
    expect(await registry.perVehicleRateLimitSeconds()).to.equal(7n);
    const p = await multisig.getProposal(0);
    expect(p.executed).to.equal(true);
  });

  it("TC-69: re-executing an already-executed proposal reverts", async () => {
    const calldata = registry.interface.encodeFunctionData("setPerVehicleRateLimit", [9]);
    await multisig.connect(sig1).propose(await registry.getAddress(), calldata, 0);
    await multisig.connect(sig2).confirm(0);
    await multisig.connect(sig2).execute(0);
    await expect(multisig.connect(sig1).execute(0))
      .to.be.revertedWithCustomError(multisig, "AlreadyExecuted");
  });

  it("TC-70: revoke() drops a confirmation and blocks execute until re-confirmed", async () => {
    const calldata = registry.interface.encodeFunctionData("setPerVehicleRateLimit", [11]);
    await multisig.connect(sig1).propose(await registry.getAddress(), calldata, 0);
    await multisig.connect(sig2).confirm(0);
    // sig2 changes their mind.
    await multisig.connect(sig2).revoke(0);
    await expect(multisig.connect(sig1).execute(0))
      .to.be.revertedWithCustomError(multisig, "BelowThreshold");
    // sig3 confirms instead — back at threshold.
    await multisig.connect(sig3).confirm(0);
    await multisig.connect(sig3).execute(0);
    expect(await registry.perVehicleRateLimitSeconds()).to.equal(11n);
  });

  it("TC-71: after admin transfer, direct onlyAdmin calls from the old admin revert", async () => {
    await expect(registry.connect(admin).setPerVehicleRateLimit(99))
      .to.be.revertedWith("Only admin can call this function");
  });

  it("TC-73: deploy.js-style full handoff — all three contracts routed through the multisig", async () => {
    // Mirror the USE_MULTISIG=1 path in scripts/deploy.js: deploy all three
    // core contracts, deploy a fresh 2-of-3 multisig, and hand over
    // admin/authority on every contract. Then verify that a call from the
    // original deployer reverts and the same call via multisig succeeds.
    const GT = await ethers.getContractFactory("GreenToken", admin);
    const greenToken = await upgrades.deployProxy(GT, [], { kind: "uups", initializer: "initialize" });
    await greenToken.waitForDeployment();

    const ER = await ethers.getContractFactory("EmissionRegistry", admin);
    const reg = await upgrades.deployProxy(ER, [], { kind: "uups", initializer: "initialize" });
    await reg.waitForDeployment();

    const PUC = await ethers.getContractFactory("PUCCertificate", admin);
    const puc = await upgrades.deployProxy(
      PUC,
      [await reg.getAddress(), await greenToken.getAddress()],
      { kind: "uups", initializer: "initialize" }
    );
    await puc.waitForDeployment();

    const MS = await ethers.getContractFactory("MultiSigAdmin", admin);
    const ms = await MS.deploy([admin.address, sig1.address, sig2.address], 2);
    await ms.waitForDeployment();
    const msAddr = await ms.getAddress();

    // Hand over every admin / authority role to the multisig.
    await (await reg.connect(admin).transferAdmin(msAddr)).wait();
    await (await greenToken.connect(admin).transferAdmin(msAddr)).wait();
    await (await puc.connect(admin).transferAuthority(msAddr)).wait();

    expect(await reg.admin()).to.equal(msAddr);
    expect(await greenToken.admin()).to.equal(msAddr);

    // Direct call from the original deployer must now revert.
    await expect(reg.connect(admin).setPerVehicleRateLimit(10))
      .to.be.revertedWith("Only admin can call this function");

    // Route the same call through a multisig proposal + 2 confirmations
    // + execute. The deployer/admin is signer[0] in the multisig, so we
    // can use (admin, sig1) as the two confirming signers.
    const calldata = reg.interface.encodeFunctionData("setPerVehicleRateLimit", [10]);
    await (await ms.connect(admin).propose(await reg.getAddress(), calldata, 0)).wait();
    // Proposer auto-confirms (count=1); sig1 adds the 2nd confirmation.
    await (await ms.connect(sig1).confirm(0)).wait();
    await (await ms.connect(admin).execute(0)).wait();

    expect(await reg.perVehicleRateLimitSeconds()).to.equal(10n);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// GreenToken reentrancy modifier sanity (audit G5/S8)
// ─────────────────────────────────────────────────────────────────────────────

describe("GreenToken.mint nonReentrant (audit G5/S8)", () => {
  it("TC-72: authorized minter can still mint after adding nonReentrant modifier", async () => {
    const [admin, minter, recipient] = await ethers.getSigners();

    const GT = await ethers.getContractFactory("GreenToken", admin);
    const greenToken = await upgrades.deployProxy(GT, [], {
      kind: "uups",
      initializer: "initialize",
    });
    await greenToken.waitForDeployment();

    // Authorize a minter (admin is initializer in GreenToken.initialize()).
    await (await greenToken.connect(admin).setMinter(minter.address, true)).wait();

    const amount = ethers.parseUnits("25", 18);
    const tx = await greenToken.connect(minter).mint(recipient.address, amount);
    await tx.wait();

    expect(await greenToken.balanceOf(recipient.address)).to.equal(amount);
    expect(await greenToken.totalRewardsMinted()).to.equal(amount);
    expect(await greenToken.rewardsEarned(recipient.address)).to.equal(amount);

    // Sanity: unauthorized callers are still rejected (the original
    // require stays before any external interaction).
    await expect(
      greenToken.connect(recipient).mint(recipient.address, amount)
    ).to.be.revertedWith("Not authorized to mint");
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// v4.1 Tiered Compliance Framework
// ─────────────────────────────────────────────────────────────────────────────

describe("v4.1 Tiered Compliance Framework", function () {
  let admin, station, device, vehicleOwner, unauthorized;
  let registry, greenToken, puc;

  // Increase timeout for tests that store many records
  this.timeout(300000);

  beforeEach(async () => {
    const [_admin, _station, _device, _vehicleOwner, _unauthorized] = await ethers.getSigners();
    admin = _admin;
    station = _station;
    device = _device;
    vehicleOwner = _vehicleOwner;
    unauthorized = _unauthorized;

    const GreenToken = await ethers.getContractFactory("GreenToken", admin);
    const greenTokenProxy = await upgrades.deployProxy(GreenToken, [], { kind: "uups", initializer: "initialize" });
    await greenTokenProxy.waitForDeployment();
    greenToken = greenTokenProxy;

    const EmissionRegistry = await ethers.getContractFactory("EmissionRegistry", admin);
    const registryProxy = await upgrades.deployProxy(EmissionRegistry, [], { kind: "uups", initializer: "initialize" });
    await registryProxy.waitForDeployment();
    registry = registryProxy;

    const PUCCertificate = await ethers.getContractFactory("PUCCertificate", admin);
    const pucProxy = await upgrades.deployProxy(
      PUCCertificate,
      [await registry.getAddress(), await greenToken.getAddress()],
      { kind: "uups", initializer: "initialize" }
    );
    await pucProxy.waitForDeployment();
    puc = pucProxy;

    // Wiring
    await (await registry.setTestingStation(station.address, true)).wait();
    await (await registry.setRegisteredDevice(device.address, true)).wait();
    await (await registry.setPUCCertificateContract(await puc.getAddress())).wait();
    await (await greenToken.setMinter(await puc.getAddress(), true)).wait();
    await (await puc.setAuthorizedIssuer(station.address, true)).wait();
  });

  /**
   * Helper: store N pass records with low pollutant values (CES ~ 5500, below Silver threshold 7500).
   */
  async function storeCleanRecords(vid, count, seedStart) {
    for (let i = 0; i < count; i++) {
      const co2 = 80000n;
      const co = 500n;
      const nox = 30n;
      const hc = 50n;
      const pm25 = 2n;
      const fraudScore = 1000n;
      const vspValue = 15000n;
      const wltcPhase = 2;
      const ts = BigInt(1700100000 + i * 100);
      const n = nonceFromSeed(seedStart + i);
      const sig = await signEmission(device, registry, vid, co2, co, nox, hc, pm25, ts, n);
      await registry
        .connect(station)
        .storeEmission(vid, co2, co, nox, hc, pm25, fraudScore, vspValue, wltcPhase, ts, n, sig);
    }
  }

  /**
   * Helper: store N pass records with very low pollutant values (CES ~ 3990, below Gold threshold 5000).
   */
  async function storeVeryCleanRecords(vid, count, seedStart) {
    for (let i = 0; i < count; i++) {
      const co2 = 60000n;
      const co = 400n;
      const nox = 20n;
      const hc = 40n;
      const pm25 = 1n;
      const fraudScore = 1000n;
      const vspValue = 15000n;
      const wltcPhase = 2;
      const ts = BigInt(1700100000 + i * 100);
      const n = nonceFromSeed(seedStart + i);
      const sig = await signEmission(device, registry, vid, co2, co, nox, hc, pm25, ts, n);
      await registry
        .connect(station)
        .storeEmission(vid, co2, co, nox, hc, pm25, fraudScore, vspValue, wltcPhase, ts, n, sig);
    }
  }

  /**
   * Helper: store a single record with a high fraud score (above threshold).
   */
  async function storeFraudRecord(vid, seedVal, tsVal) {
    const co2 = 80000n;
    const co = 500n;
    const nox = 30n;
    const hc = 50n;
    const pm25 = 2n;
    const fraudScore = 7500n;
    const vspValue = 15000n;
    const wltcPhase = 2;
    const ts = BigInt(tsVal || 1700200000);
    const n = nonceFromSeed(seedVal);
    const sig = await signEmission(device, registry, vid, co2, co, nox, hc, pm25, ts, n);
    await registry
      .connect(station)
      .storeEmission(vid, co2, co, nox, hc, pm25, fraudScore, vspValue, wltcPhase, ts, n, sig);
  }

  it("TC-74: After 5 PASS records, vehicle tier auto-upgrades to Bronze", async () => {
    const vid = "TIER_BRONZE";
    expect(await registry.getVehicleTier(vid)).to.equal(0);
    await storeCleanRecords(vid, 5, 5000);
    expect(await registry.getVehicleTier(vid)).to.equal(1);
  });

  it("TC-75: After 20 PASS records with low CES (<7500), tier upgrades to Silver", async () => {
    const vid = "TIER_SILVER";
    await storeCleanRecords(vid, 20, 5100);
    expect(await registry.getVehicleTier(vid)).to.equal(2);
  });

  it("TC-76: After 50 PASS records with very low CES (<5000) and 0 fraud, tier upgrades to Gold", async () => {
    const vid = "TIER_GOLD";
    await storeVeryCleanRecords(vid, 50, 5200);
    expect(await registry.getVehicleTier(vid)).to.equal(3);
  });

  it("TC-77: getVehicleTier() returns correct tier value", async () => {
    const vid = "TIER_QUERY";
    expect(await registry.getVehicleTier(vid)).to.equal(0);
    await storeCleanRecords(vid, 5, 5300);
    expect(await registry.getVehicleTier(vid)).to.equal(1);
    await storeCleanRecords(vid, 15, 5305);
    expect(await registry.getVehicleTier(vid)).to.equal(2);
  });

  it("TC-78: setVehicleTierManually() by admin works; non-admin reverts", async () => {
    const vid = "TIER_MANUAL";
    await storeCleanRecords(vid, 1, 5400);
    await registry.connect(admin).setVehicleTierManually(vid, 3);
    expect(await registry.getVehicleTier(vid)).to.equal(3);
    await expect(
      registry.connect(unauthorized).setVehicleTierManually(vid, 2)
    ).to.be.revertedWith("Only admin can call this function");
  });

  it("TC-79: A fraud alert prevents Gold tier (downgrades to Silver or Bronze)", async () => {
    const vid = "TIER_FRAUD";
    await storeVeryCleanRecords(vid, 49, 5500);
    await storeFraudRecord(vid, 5549, 1700100000 + 49 * 100);
    const tier = await registry.getVehicleTier(vid);
    expect(tier).to.be.lte(2);
    expect(tier).to.be.gte(1);
  });

  it("TC-80: PUC certificate for Gold-tier vehicle gets 730-day validity", async () => {
    const vid = "TIER_GOLD_PUC";
    await storeVeryCleanRecords(vid, 50, 5600);
    expect(await registry.getVehicleTier(vid)).to.equal(3);
    await registry.connect(admin).setVehicleOwner(vid, vehicleOwner.address);
    const tx = await puc
      .connect(station)
      .issueCertificateWithFirstFlag(vid, vehicleOwner.address, "", false);
    const rcpt = await tx.wait();
    const issued = rcpt.logs.map((l) => {
      try { return puc.interface.parseLog(l); } catch { return null; }
    }).find((e) => e && e.name === "CertificateIssued");
    const tokenId = issued.args.tokenId;
    const cert = await puc.getCertificate(tokenId);
    const duration = BigInt(cert.expiryTimestamp) - BigInt(cert.issueTimestamp);
    expect(duration).to.equal(730n * 24n * 60n * 60n);
    expect(cert.complianceTier).to.equal(3);
  });

  it("TC-81: PUC certificate for Silver-tier vehicle gets 365-day validity", async () => {
    const vid = "TIER_SILVER_PUC";
    await storeCleanRecords(vid, 20, 5700);
    expect(await registry.getVehicleTier(vid)).to.equal(2);
    await registry.connect(admin).setVehicleOwner(vid, vehicleOwner.address);
    const tx = await puc
      .connect(station)
      .issueCertificateWithFirstFlag(vid, vehicleOwner.address, "", false);
    const rcpt = await tx.wait();
    const issued = rcpt.logs.map((l) => {
      try { return puc.interface.parseLog(l); } catch { return null; }
    }).find((e) => e && e.name === "CertificateIssued");
    const tokenId = issued.args.tokenId;
    const cert = await puc.getCertificate(tokenId);
    const duration = BigInt(cert.expiryTimestamp) - BigInt(cert.issueTimestamp);
    expect(duration).to.equal(365n * 24n * 60n * 60n);
    expect(cert.complianceTier).to.equal(2);
  });

  it("TC-82: First PUC always gets 360-day validity regardless of tier", async () => {
    const vid = "TIER_FIRST_PUC";
    await storeVeryCleanRecords(vid, 50, 5800);
    expect(await registry.getVehicleTier(vid)).to.equal(3);
    await registry.connect(admin).setVehicleOwner(vid, vehicleOwner.address);
    const tx = await puc
      .connect(station)
      ["issueCertificate(string,address,string)"](vid, vehicleOwner.address, "");
    const rcpt = await tx.wait();
    const issued = rcpt.logs.map((l) => {
      try { return puc.interface.parseLog(l); } catch { return null; }
    }).find((e) => e && e.name === "CertificateIssued");
    const tokenId = issued.args.tokenId;
    const cert = await puc.getCertificate(tokenId);
    const duration = BigInt(cert.expiryTimestamp) - BigInt(cert.issueTimestamp);
    expect(duration).to.equal(360n * 24n * 60n * 60n);
    expect(cert.isFirstPUC).to.equal(true);
  });

  // ═══════════════════════════════════════════════════════════════════════
  describe("v4.1.1 Audit fixes", function () {
    it("TC-83: transferAdmin emits AdminTransferred event on EmissionRegistry", async () => {
      const [, , , , , newAdmin] = await ethers.getSigners();
      await expect(registry.connect(admin).transferAdmin(newAdmin.address))
        .to.emit(registry, "AdminTransferred")
        .withArgs(admin.address, newAdmin.address);
      expect(await registry.admin()).to.equal(newAdmin.address);
    });

    it("TC-84: transferAuthority emits AuthorityTransferred event on PUCCertificate", async () => {
      const [, , , , , newAuthority] = await ethers.getSigners();
      await expect(puc.connect(admin).transferAuthority(newAuthority.address))
        .to.emit(puc, "AuthorityTransferred")
        .withArgs(admin.address, newAuthority.address);
      expect(await puc.authority()).to.equal(newAuthority.address);
    });

    it("TC-85: transferAdmin emits AdminTransferred event on GreenToken", async () => {
      const [, , , , , newAdmin] = await ethers.getSigners();
      await expect(greenToken.connect(admin).transferAdmin(newAdmin.address))
        .to.emit(greenToken, "AdminTransferred")
        .withArgs(admin.address, newAdmin.address);
      expect(await greenToken.admin()).to.equal(newAdmin.address);
    });

    it("TC-86: PUC certificate is non-transferable (soul-bound)", async () => {
      const vid = "SBT_TEST_VEH";
      // Store 3 consecutive pass records for eligibility
      for (let i = 0; i < 3; i++) {
        await storePassRecord(registry, vid, station, device, 9000 + i, 1700200000 + i * 100);
      }
      // Set vehicle owner
      await registry.connect(admin).setVehicleOwner(vid, vehicleOwner.address);
      // Issue certificate
      const tx = await puc
        .connect(station)
        ["issueCertificate(string,address,string)"](vid, vehicleOwner.address, "");
      const rcpt = await tx.wait();
      const issued = rcpt.logs.map((l) => {
        try { return puc.interface.parseLog(l); } catch { return null; }
      }).find((e) => e && e.name === "CertificateIssued");
      const tokenId = issued.args.tokenId;

      // Attempt to transfer — should revert (soul-bound)
      const [, , , , , recipient] = await ethers.getSigners();
      await expect(
        puc.connect(vehicleOwner).transferFrom(vehicleOwner.address, recipient.address, tokenId)
      ).to.be.revertedWith("PUC certificates are non-transferable (soul-bound)");

      // Also test safeTransferFrom
      await expect(
        puc.connect(vehicleOwner)["safeTransferFrom(address,address,uint256)"](
          vehicleOwner.address, recipient.address, tokenId
        )
      ).to.be.revertedWith("PUC certificates are non-transferable (soul-bound)");
    });
  });
});
