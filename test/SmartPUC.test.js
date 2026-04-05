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
 * Sign an emission payload the same way the Solidity side hashes it:
 *     keccak256(vehicleId || co2 || co || nox || hc || pm25 || timestamp || nonce)
 * then eth-sign the hash. Matches EmissionRegistry.getMessageHash.
 */
async function signEmission(signer, vehicleId, co2, co, nox, hc, pm25, timestamp, nonce) {
  const hash = ethers.solidityPackedKeccak256(
    ["string", "uint256", "uint256", "uint256", "uint256", "uint256", "uint256", "bytes32"],
    [vehicleId, co2, co, nox, hc, pm25, timestamp, nonce]
  );
  // ethers v6 signMessage automatically applies the Ethereum-signed-message prefix.
  return signer.signMessage(ethers.getBytes(hash));
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
  const sig = await signEmission(deviceSigner, vehicleId, co2, co, nox, hc, pm25, ts, n);
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
  const sig = await signEmission(deviceSigner, vehicleId, co2, co, nox, hc, pm25, ts, n);
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
      const sig = await signEmission(device, vid, co2, co, nox, hc, pm25, ts, n);

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
      const sig = await signEmission(device, "MH12XX0000", 80000n, 500n, 30n, 50n, 2n, 1700001000n, n);
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
      const sig = await signEmission(device, "", 80000n, 500n, 30n, 50n, 2n, 1700000900n, n);
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
      const sig = await signEmission(device, vid, co2, co, nox, hc, pm25, ts, n);

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
      const sig = await signEmission(device, vid, 80000n, 500n, 30n, 50n, 2n, ts, n);
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
      const sig = await signEmission(unauthorized, vid, 80000n, 500n, 30n, 50n, 2n, ts, n);
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
});
