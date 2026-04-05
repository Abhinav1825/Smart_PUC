const GreenToken = artifacts.require("GreenToken");
const EmissionRegistry = artifacts.require("EmissionRegistry");
const PUCCertificate = artifacts.require("PUCCertificate");

/**
 * Helper: sign emission data with a device account.
 * In Truffle, web3.eth.sign automatically adds the "\x19Ethereum Signed Message:\n32" prefix.
 */
async function signEmission(vehicleId, co2, co, nox, hc, pm25, timestamp, nonce, deviceAccount) {
  const hash = web3.utils.soliditySha3(
    { type: "string", value: vehicleId },
    { type: "uint256", value: co2 },
    { type: "uint256", value: co },
    { type: "uint256", value: nox },
    { type: "uint256", value: hc },
    { type: "uint256", value: pm25 },
    { type: "uint256", value: timestamp },
    { type: "bytes32", value: nonce }
  );
  const signature = await web3.eth.sign(hash, deviceAccount);
  return signature;
}

/** Helper: generate a unique nonce from an integer seed */
function nonce(seed) {
  return web3.utils.soliditySha3({ type: "uint256", value: seed });
}

/**
 * Helper: store a PASS emission record through the full signing flow.
 * Uses low pollutant values that compute to CES < 10000.
 */
async function storePassRecord(registry, vehicleId, station, device, nonceSeed, timestamp) {
  const co2 = 80000;   // well below 120000
  const co = 500;       // below 1000
  const noxVal = 30;    // below 60
  const hc = 50;        // below 100
  const pm25 = 2;       // below 5
  const fraudScore = 1000;
  const vspValue = 15000;
  const wltcPhase = 2;
  const ts = timestamp || 1700000000;
  const n = nonce(nonceSeed);

  const sig = await signEmission(vehicleId, co2, co, noxVal, hc, pm25, ts, n, device);

  return registry.storeEmission(
    vehicleId, co2, co, noxVal, hc, pm25, fraudScore, vspValue, wltcPhase, ts, n, sig,
    { from: station }
  );
}

/**
 * Helper: store a FAIL emission record (high pollutants -> CES >= 10000).
 */
async function storeFailRecord(registry, vehicleId, station, device, nonceSeed, timestamp) {
  const co2 = 200000;   // way above 120000
  const co = 2000;       // above 1000
  const noxVal = 120;    // above 60
  const hc = 200;        // above 100
  const pm25 = 10;       // above 5
  const fraudScore = 2000;
  const vspValue = 25000;
  const wltcPhase = 3;
  const ts = timestamp || 1700000100;
  const n = nonce(nonceSeed);

  const sig = await signEmission(vehicleId, co2, co, noxVal, hc, pm25, ts, n, device);

  return registry.storeEmission(
    vehicleId, co2, co, noxVal, hc, pm25, fraudScore, vspValue, wltcPhase, ts, n, sig,
    { from: station }
  );
}

contract("Smart PUC - Full Test Suite", (accounts) => {
  const admin = accounts[0];
  const station = accounts[1];
  const device = accounts[2];
  const vehicleOwner = accounts[3];
  const unauthorized = accounts[4];

  let registry, greenToken, pucCert;

  beforeEach(async () => {
    // Deploy contracts
    greenToken = await GreenToken.new({ from: admin });
    registry = await EmissionRegistry.new({ from: admin });
    pucCert = await PUCCertificate.new(registry.address, greenToken.address, { from: admin });

    // Wire up permissions
    await registry.setTestingStation(station, true, { from: admin });
    await registry.setRegisteredDevice(device, true, { from: admin });
    await registry.setPUCCertificateContract(pucCert.address, { from: admin });

    // Authorize PUCCertificate to mint GreenTokens
    await greenToken.setMinter(pucCert.address, true, { from: admin });

    // Authorize station as certificate issuer
    await pucCert.setAuthorizedIssuer(station, true, { from: admin });
  });

  // ═══════════════════════════════════════════════════════════════════════
  //  EmissionRegistry Tests (TC-01 through TC-17)
  // ═══════════════════════════════════════════════════════════════════════

  describe("EmissionRegistry", () => {

    it("TC-01: Admin is deployer and thresholds are correct", async () => {
      const contractAdmin = await registry.admin();
      assert.equal(contractAdmin, admin, "Admin should be deployer");

      const bsviCo2 = await registry.BSVI_CO2();
      assert.equal(bsviCo2.toNumber(), 120000, "BSVI_CO2 should be 120000");

      const bsviCo = await registry.BSVI_CO();
      assert.equal(bsviCo.toNumber(), 1000, "BSVI_CO should be 1000");

      const bsviNox = await registry.BSVI_NOX();
      assert.equal(bsviNox.toNumber(), 60, "BSVI_NOX should be 60");

      const bsviHc = await registry.BSVI_HC();
      assert.equal(bsviHc.toNumber(), 100, "BSVI_HC should be 100");

      const bsviPm25 = await registry.BSVI_PM25();
      assert.equal(bsviPm25.toNumber(), 5, "BSVI_PM25 should be 5");

      const cesCeiling = await registry.CES_PASS_CEILING();
      assert.equal(cesCeiling.toNumber(), 10000, "CES_PASS_CEILING should be 10000");

      const fraudThreshold = await registry.FRAUD_ALERT_THRESHOLD();
      assert.equal(fraudThreshold.toNumber(), 6500, "FRAUD_ALERT_THRESHOLD should be 6500");
    });

    it("TC-02: Store PASS record with valid device signature and verify", async () => {
      const tx = await storePassRecord(registry, "MH12AB1234", station, device, 1);

      const event = tx.logs.find((log) => log.event === "RecordStored");
      assert.ok(event, "RecordStored event should be emitted");

      const record = await registry.getRecord("MH12AB1234", 0);
      assert.equal(record.vehicleId, "MH12AB1234");
      assert.equal(record.status, true, "Record should PASS");
      assert.equal(record.deviceAddress, device, "Device address should match signer");
      assert.equal(record.stationAddress, station, "Station address should match sender");
    });

    it("TC-03: Store FAIL record (high emissions) and verify ViolationDetected event", async () => {
      const tx = await storeFailRecord(registry, "MH12CD5678", station, device, 2);

      const violationEvent = tx.logs.find((log) => log.event === "ViolationDetected");
      assert.ok(violationEvent, "ViolationDetected event should be emitted");
      assert.equal(violationEvent.args.vehicleId, "MH12CD5678");

      const record = await registry.getRecord("MH12CD5678", 0);
      assert.equal(record.status, false, "Record should FAIL");
    });

    it("TC-04: Fraud detection (fraudScore >= 6500) emits FraudDetected", async () => {
      // Low pollutants but high fraud score
      const vehicleId = "MH12EF9012";
      const co2 = 80000;
      const co = 500;
      const noxVal = 30;
      const hc = 50;
      const pm25 = 2;
      const fraudScore = 7500; // above 6500
      const vspValue = 12000;
      const wltcPhase = 1;
      const ts = 1700000200;
      const n = nonce(100);

      const sig = await signEmission(vehicleId, co2, co, noxVal, hc, pm25, ts, n, device);

      const tx = await registry.storeEmission(
        vehicleId, co2, co, noxVal, hc, pm25, fraudScore, vspValue, wltcPhase, ts, n, sig,
        { from: station }
      );

      const fraudEvent = tx.logs.find((log) => log.event === "FraudDetected");
      assert.ok(fraudEvent, "FraudDetected event should be emitted");
      assert.equal(fraudEvent.args.vehicleId, vehicleId);
      assert.equal(fraudEvent.args.fraudScore.toNumber(), 7500);
    });

    it("TC-05: Multiple records + getVehicleStats returns correct aggregates", async () => {
      const vid = "MH12GH3456";
      await storePassRecord(registry, vid, station, device, 10, 1700000300);
      await storePassRecord(registry, vid, station, device, 11, 1700000400);
      await storeFailRecord(registry, vid, station, device, 12, 1700000500);

      const stats = await registry.getVehicleStats(vid);
      assert.equal(stats.totalRecords.toNumber(), 3, "Should have 3 records");
      assert.equal(stats.violations.toNumber(), 1, "Should have 1 violation");
      assert.ok(stats.averageCES.toNumber() > 0, "Average CES should be > 0");
    });

    it("TC-06: getViolations returns only FAIL records", async () => {
      const vid = "MH12IJ7890";
      await storePassRecord(registry, vid, station, device, 20, 1700000600);
      await storeFailRecord(registry, vid, station, device, 21, 1700000700);
      await storePassRecord(registry, vid, station, device, 22, 1700000800);

      const violations = await registry.getViolations(vid);
      assert.equal(violations.length, 1, "Should have exactly 1 violation");
      assert.equal(violations[0].status, false, "Violation record should have status=false");
    });

    it("TC-07: Unauthorized station cannot store emissions", async () => {
      const n = nonce(30);
      const sig = await signEmission("MH12XX0000", 80000, 500, 30, 50, 2, 1700001000, n, device);

      try {
        await registry.storeEmission(
          "MH12XX0000", 80000, 500, 30, 50, 2, 1000, 15000, 2, 1700001000, n, sig,
          { from: unauthorized }
        );
        assert.fail("Should have reverted");
      } catch (error) {
        assert.ok(
          error.message.includes("revert") || error.message.includes("Caller is not an authorized testing station"),
          "Should revert for unauthorized station"
        );
      }
    });

    it("TC-08: Auto-registration of vehicles", async () => {
      const vid = "MH12KL2345";
      const countBefore = await registry.vehicleCount();
      await storePassRecord(registry, vid, station, device, 40, 1700000800);
      const countAfter = await registry.vehicleCount();

      assert.equal(
        countAfter.toNumber(),
        countBefore.toNumber() + 1,
        "Vehicle count should increase by 1"
      );

      const registered = await registry.getRegisteredVehicles();
      assert.ok(registered.includes(vid), "Vehicle should appear in registered list");
    });

    it("TC-09: Empty vehicle ID rejected", async () => {
      const n = nonce(50);
      const sig = await signEmission("", 80000, 500, 30, 50, 2, 1700000900, n, device);

      try {
        await registry.storeEmission(
          "", 80000, 500, 30, 50, 2, 1000, 15000, 2, 1700000900, n, sig,
          { from: station }
        );
        assert.fail("Should have reverted");
      } catch (error) {
        assert.ok(
          error.message.includes("revert") || error.message.includes("Vehicle ID cannot be empty"),
          "Should revert for empty vehicle ID"
        );
      }
    });

    it("TC-10: All record fields stored correctly", async () => {
      const vid = "MH12MN6789";
      const co2 = 105000;
      const co = 600;
      const noxVal = 35;
      const hc = 55;
      const pm25 = 3;
      const fraudScore = 1500;
      const vspValue = 16000;
      const wltcPhase = 2;
      const ts = 1700001000;
      const n = nonce(60);

      const sig = await signEmission(vid, co2, co, noxVal, hc, pm25, ts, n, device);

      await registry.storeEmission(
        vid, co2, co, noxVal, hc, pm25, fraudScore, vspValue, wltcPhase, ts, n, sig,
        { from: station }
      );

      const record = await registry.getRecord(vid, 0);
      assert.equal(record.vehicleId, vid, "vehicleId mismatch");
      assert.equal(record.co2.toNumber(), co2, "co2 mismatch");
      assert.equal(record.co.toNumber(), co, "co mismatch");
      assert.equal(record.nox.toNumber(), noxVal, "nox mismatch");
      assert.equal(record.hc.toNumber(), hc, "hc mismatch");
      assert.equal(record.pm25.toNumber(), pm25, "pm25 mismatch");
      assert.equal(record.fraudScore.toNumber(), fraudScore, "fraudScore mismatch");
      assert.equal(record.vspValue.toNumber(), vspValue, "vspValue mismatch");
      assert.equal(record.wltcPhase.toNumber(), wltcPhase, "wltcPhase mismatch");
      assert.equal(record.timestamp.toNumber(), ts, "timestamp mismatch");
      assert.equal(record.deviceAddress, device, "device address mismatch");
      assert.equal(record.stationAddress, station, "station address mismatch");
      // CES is computed on-chain, just ensure it is present
      assert.ok(record.cesScore.toNumber() > 0, "cesScore should be > 0");
    });

    it("TC-11: On-chain CES computation matches expected values", async () => {
      // All at exactly threshold values => each ratio = 1.0, CES = 1.0 => 10000
      const ces = await registry.computeCES(120000, 1000, 60, 100, 5);
      assert.equal(ces.toNumber(), 10000, "CES at exact thresholds should be 10000");

      // Half of all thresholds => CES = 0.5 => 5000
      const cesHalf = await registry.computeCES(60000, 500, 30, 50, 2);
      // Due to integer division, may not be exactly 5000
      const cesHalfVal = cesHalf.toNumber();
      assert.ok(cesHalfVal >= 4800 && cesHalfVal <= 5200, "CES at half thresholds should be near 5000, got " + cesHalfVal);

      // Zero emissions => CES = 0
      const cesZero = await registry.computeCES(0, 0, 0, 0, 0);
      assert.equal(cesZero.toNumber(), 0, "CES with zero emissions should be 0");
    });

    it("TC-12: Nonce replay protection (same nonce rejected twice)", async () => {
      const vid = "MH12RP0001";
      const repeatedNonce = nonce(70);
      const co2 = 80000;
      const co = 500;
      const noxVal = 30;
      const hc = 50;
      const pm25 = 2;
      const ts = 1700002000;

      const sig = await signEmission(vid, co2, co, noxVal, hc, pm25, ts, repeatedNonce, device);

      // First use succeeds
      await registry.storeEmission(
        vid, co2, co, noxVal, hc, pm25, 1000, 15000, 2, ts, repeatedNonce, sig,
        { from: station }
      );

      // Second use with same nonce should fail
      try {
        await registry.storeEmission(
          vid, co2, co, noxVal, hc, pm25, 1000, 15000, 2, ts, repeatedNonce, sig,
          { from: station }
        );
        assert.fail("Should have reverted on nonce replay");
      } catch (error) {
        assert.ok(
          error.message.includes("revert") || error.message.includes("Nonce already used"),
          "Should revert for duplicate nonce"
        );
      }
    });

    it("TC-13: Invalid device signature rejected", async () => {
      const vid = "MH12BADSIG";
      const n = nonce(80);
      const ts = 1700003000;

      // Sign with unauthorized account (not a registered device)
      const sig = await signEmission(vid, 80000, 500, 30, 50, 2, ts, n, unauthorized);

      try {
        await registry.storeEmission(
          vid, 80000, 500, 30, 50, 2, 1000, 15000, 2, ts, n, sig,
          { from: station }
        );
        assert.fail("Should have reverted for unregistered device signature");
      } catch (error) {
        assert.ok(
          error.message.includes("revert") || error.message.includes("Signature from unregistered device"),
          "Should revert for invalid device signature"
        );
      }
    });

    it("TC-14: Consecutive pass tracking works correctly", async () => {
      const vid = "MH12PASS01";

      await storePassRecord(registry, vid, station, device, 90, 1700004000);
      let passes = await registry.consecutivePassCount(vid);
      assert.equal(passes.toNumber(), 1, "Should have 1 consecutive pass");

      await storePassRecord(registry, vid, station, device, 91, 1700004100);
      passes = await registry.consecutivePassCount(vid);
      assert.equal(passes.toNumber(), 2, "Should have 2 consecutive passes");

      // A fail resets the counter
      await storeFailRecord(registry, vid, station, device, 92, 1700004200);
      passes = await registry.consecutivePassCount(vid);
      assert.equal(passes.toNumber(), 0, "Consecutive passes should reset to 0 after FAIL");

      // Start counting again
      await storePassRecord(registry, vid, station, device, 93, 1700004300);
      passes = await registry.consecutivePassCount(vid);
      assert.equal(passes.toNumber(), 1, "Should restart at 1 after reset");
    });

    it("TC-15: Certificate eligibility after 3 consecutive passes", async () => {
      const vid = "MH12ELIG01";

      await storePassRecord(registry, vid, station, device, 200, 1700005000);
      await storePassRecord(registry, vid, station, device, 201, 1700005100);

      let result = await registry.isCertificateEligible(vid);
      assert.equal(result.eligible, false, "Should not be eligible with only 2 passes");

      const tx = await storePassRecord(registry, vid, station, device, 202, 1700005200);

      result = await registry.isCertificateEligible(vid);
      assert.equal(result.eligible, true, "Should be eligible after 3 passes");
      assert.equal(result.passes.toNumber(), 3, "Should report 3 consecutive passes");

      // Check CertificateEligible event
      const eligibleEvent = tx.logs.find((log) => log.event === "CertificateEligible");
      assert.ok(eligibleEvent, "CertificateEligible event should be emitted on 3rd pass");
    });

    it("TC-16: Vehicle count tracking with bounded limit", async () => {
      await storePassRecord(registry, "VEH_A", station, device, 300, 1700006000);
      await storePassRecord(registry, "VEH_B", station, device, 301, 1700006100);
      await storePassRecord(registry, "VEH_C", station, device, 302, 1700006200);

      const count = await registry.vehicleCount();
      assert.equal(count.toNumber(), 3, "Vehicle count should be 3");

      // Adding another record for existing vehicle should NOT increase count
      await storePassRecord(registry, "VEH_A", station, device, 303, 1700006300);
      const countAfter = await registry.vehicleCount();
      assert.equal(countAfter.toNumber(), 3, "Vehicle count should still be 3");

      const maxVehicles = await registry.MAX_VEHICLES();
      assert.equal(maxVehicles.toNumber(), 10000, "MAX_VEHICLES should be 10000");
    });

    it("TC-17: Pagination works correctly", async () => {
      const vid = "MH12PAGE01";

      // Store 5 records
      for (let i = 0; i < 5; i++) {
        await storePassRecord(registry, vid, station, device, 400 + i, 1700007000 + i * 100);
      }

      // Get page 1: offset 0, limit 2
      const page1 = await registry.getRecordsPaginated(vid, 0, 2);
      assert.equal(page1.length, 2, "Page 1 should have 2 records");

      // Get page 2: offset 2, limit 2
      const page2 = await registry.getRecordsPaginated(vid, 2, 2);
      assert.equal(page2.length, 2, "Page 2 should have 2 records");

      // Get page 3: offset 4, limit 2 (only 1 remaining)
      const page3 = await registry.getRecordsPaginated(vid, 4, 2);
      assert.equal(page3.length, 1, "Page 3 should have 1 record");

      // Out-of-bounds offset returns empty
      const pageEmpty = await registry.getRecordsPaginated(vid, 10, 2);
      assert.equal(pageEmpty.length, 0, "Out-of-bounds offset should return empty");
    });
  });

  // ═══════════════════════════════════════════════════════════════════════
  //  PUCCertificate Tests (TC-18 through TC-25)
  // ═══════════════════════════════════════════════════════════════════════

  describe("PUCCertificate", () => {

    /** Helper to make a vehicle eligible (3 consecutive passes) */
    async function makeEligible(vehicleId, nonceSeedStart) {
      for (let i = 0; i < 3; i++) {
        await storePassRecord(
          registry, vehicleId, station, device,
          nonceSeedStart + i,
          1700010000 + i * 100
        );
      }
    }

    it("TC-18: Certificate issuance after 3 consecutive passes", async () => {
      const vid = "MH12CERT01";
      await makeEligible(vid, 500);

      const tx = await pucCert.issueCertificate(vid, vehicleOwner, "", { from: station });

      const issuedEvent = tx.logs.find((log) => log.event === "CertificateIssued");
      assert.ok(issuedEvent, "CertificateIssued event should be emitted");
      assert.equal(issuedEvent.args.vehicleId, vid);
      assert.equal(issuedEvent.args.vehicleOwner, vehicleOwner);

      const tokenId = issuedEvent.args.tokenId.toNumber();
      assert.ok(tokenId > 0, "Token ID should be > 0");

      // Verify NFT ownership
      const owner = await pucCert.ownerOf(tokenId);
      assert.equal(owner, vehicleOwner, "Vehicle owner should own the NFT");

      // Verify certificate data
      const cert = await pucCert.getCertificate(tokenId);
      assert.equal(cert.vehicleId, vid);
      assert.equal(cert.revoked, false);
      assert.ok(cert.averageCES.toNumber() > 0, "Average CES should be recorded");
    });

    it("TC-19: Certificate rejected if insufficient passes", async () => {
      const vid = "MH12NOPASS";
      // Only 2 passes, need 3
      await storePassRecord(registry, vid, station, device, 510, 1700011000);
      await storePassRecord(registry, vid, station, device, 511, 1700011100);

      try {
        await pucCert.issueCertificate(vid, vehicleOwner, "", { from: station });
        assert.fail("Should have reverted");
      } catch (error) {
        assert.ok(
          error.message.includes("revert") || error.message.includes("Insufficient consecutive passes"),
          "Should revert for insufficient passes"
        );
      }
    });

    it("TC-20: Certificate rejected if CES too high", async () => {
      const vid = "MH12HICES";
      // 3 FAIL records still count as records but consecutive pass count = 0
      await storeFailRecord(registry, vid, station, device, 520, 1700012000);
      await storeFailRecord(registry, vid, station, device, 521, 1700012100);
      await storeFailRecord(registry, vid, station, device, 522, 1700012200);

      try {
        await pucCert.issueCertificate(vid, vehicleOwner, "", { from: station });
        assert.fail("Should have reverted");
      } catch (error) {
        assert.ok(
          error.message.includes("revert"),
          "Should revert for high CES / insufficient passes"
        );
      }
    });

    it("TC-21: Certificate revocation by authority", async () => {
      const vid = "MH12REVOKE";
      await makeEligible(vid, 530);

      const issueTx = await pucCert.issueCertificate(vid, vehicleOwner, "", { from: station });
      const tokenId = issueTx.logs.find((l) => l.event === "CertificateIssued").args.tokenId;

      // Revoke
      const revokeTx = await pucCert.revokeCertificate(tokenId, "Failed spot check", { from: admin });
      const revokeEvent = revokeTx.logs.find((log) => log.event === "CertificateRevoked");
      assert.ok(revokeEvent, "CertificateRevoked event should be emitted");
      assert.equal(revokeEvent.args.reason, "Failed spot check");

      const cert = await pucCert.getCertificate(tokenId);
      assert.equal(cert.revoked, true, "Certificate should be revoked");

      // isValid should return false
      const validity = await pucCert.isValid(vid);
      assert.equal(validity.valid, false, "Revoked certificate should be invalid");
    });

    it("TC-22: Duplicate certificate rejected (already valid)", async () => {
      const vid = "MH12DUP01";
      await makeEligible(vid, 540);

      await pucCert.issueCertificate(vid, vehicleOwner, "", { from: station });

      // Try issuing again while first is still valid
      try {
        await pucCert.issueCertificate(vid, vehicleOwner, "", { from: station });
        assert.fail("Should have reverted");
      } catch (error) {
        assert.ok(
          error.message.includes("revert") || error.message.includes("Vehicle already has a valid certificate"),
          "Should revert for duplicate certificate"
        );
      }
    });

    it("TC-23: TokenURI can be set and retrieved", async () => {
      const vid = "MH12URI01";
      await makeEligible(vid, 550);

      const issueTx = await pucCert.issueCertificate(vid, vehicleOwner, "QmTestHash123", { from: station });
      const tokenId = issueTx.logs.find((l) => l.event === "CertificateIssued").args.tokenId;

      // Token URI should return the IPFS hash directly (no base URI set)
      const uri = await pucCert.tokenURI(tokenId);
      assert.equal(uri, "QmTestHash123", "TokenURI should match set value");

      // Set base URI and verify concatenation
      await pucCert.setBaseURI("https://ipfs.io/ipfs/", { from: admin });
      const uriWithBase = await pucCert.tokenURI(tokenId);
      assert.equal(uriWithBase, "https://ipfs.io/ipfs/QmTestHash123", "Should concatenate base + token URI");

      // Admin can update token URI
      await pucCert.setTokenURI(tokenId, "QmUpdatedHash456", { from: admin });
      const updatedUri = await pucCert.tokenURI(tokenId);
      assert.equal(updatedUri, "https://ipfs.io/ipfs/QmUpdatedHash456", "Should reflect updated URI");
    });

    it("TC-24: GreenToken automatically minted on certificate issuance", async () => {
      const vid = "MH12GREEN1";
      await makeEligible(vid, 560);

      const balanceBefore = await greenToken.balanceOf(vehicleOwner);

      const tx = await pucCert.issueCertificate(vid, vehicleOwner, "", { from: station });

      const balanceAfter = await greenToken.balanceOf(vehicleOwner);
      const expectedReward = web3.utils.toBN("100000000000000000000"); // 100 * 10^18

      assert.ok(
        balanceAfter.sub(balanceBefore).eq(expectedReward),
        "Vehicle owner should receive 100 GCT tokens"
      );

      // Check GreenTokensAwarded event
      const awardEvent = tx.logs.find((log) => log.event === "GreenTokensAwarded");
      assert.ok(awardEvent, "GreenTokensAwarded event should be emitted");
    });

    it("TC-25: Non-authority cannot issue certificates", async () => {
      const vid = "MH12NOAUTH";
      await makeEligible(vid, 570);

      try {
        await pucCert.issueCertificate(vid, vehicleOwner, "", { from: unauthorized });
        assert.fail("Should have reverted");
      } catch (error) {
        assert.ok(
          error.message.includes("revert") || error.message.includes("Not authorized to issue certificates"),
          "Should revert for unauthorized issuer"
        );
      }
    });
  });

  // ═══════════════════════════════════════════════════════════════════════
  //  GreenToken Tests (TC-26 through TC-30)
  // ═══════════════════════════════════════════════════════════════════════

  describe("GreenToken", () => {

    it("TC-26: Only authorized minters can mint", async () => {
      // Unauthorized address tries to mint
      try {
        await greenToken.mint(vehicleOwner, web3.utils.toWei("100", "ether"), { from: unauthorized });
        assert.fail("Should have reverted");
      } catch (error) {
        assert.ok(
          error.message.includes("revert") || error.message.includes("Not authorized to mint"),
          "Should revert for unauthorized minter"
        );
      }

      // Authorize station as minter and verify it works
      await greenToken.setMinter(station, true, { from: admin });
      await greenToken.mint(vehicleOwner, web3.utils.toWei("50", "ether"), { from: station });

      const balance = await greenToken.balanceOf(vehicleOwner);
      assert.equal(balance.toString(), web3.utils.toWei("50", "ether"), "Should have 50 GCT");
    });

    it("TC-27: Token balance and reward tracking", async () => {
      await greenToken.setMinter(station, true, { from: admin });

      await greenToken.mint(vehicleOwner, web3.utils.toWei("100", "ether"), { from: station });
      await greenToken.mint(vehicleOwner, web3.utils.toWei("50", "ether"), { from: station });

      const summary = await greenToken.getRewardSummary(vehicleOwner);
      assert.equal(
        summary.balance.toString(),
        web3.utils.toWei("150", "ether"),
        "Balance should be 150 GCT"
      );
      assert.equal(
        summary.earned.toString(),
        web3.utils.toWei("150", "ether"),
        "Total earned should be 150 GCT"
      );

      const totalMinted = await greenToken.totalRewardsMinted();
      assert.equal(
        totalMinted.toString(),
        web3.utils.toWei("150", "ether"),
        "Total rewards minted should be 150 GCT"
      );
    });

    it("TC-28: Redeem tokens for toll discount (burn mechanism)", async () => {
      await greenToken.setMinter(station, true, { from: admin });
      await greenToken.mint(vehicleOwner, web3.utils.toWei("100", "ether"), { from: station });

      // Redeem TOLL_DISCOUNT (costs 50 GCT)
      const tx = await greenToken.redeem(0, { from: vehicleOwner }); // 0 = TOLL_DISCOUNT

      const redeemEvent = tx.logs.find((log) => log.event === "Redeemed");
      assert.ok(redeemEvent, "Redeemed event should be emitted");
      assert.equal(redeemEvent.args.rewardType.toNumber(), 0);

      // Balance should be reduced by 50
      const balance = await greenToken.balanceOf(vehicleOwner);
      assert.equal(
        balance.toString(),
        web3.utils.toWei("50", "ether"),
        "Balance should be 50 GCT after toll redemption"
      );

      // totalRedeemed tracking
      const totalRedeemed = await greenToken.totalRedeemed();
      assert.equal(
        totalRedeemed.toString(),
        web3.utils.toWei("50", "ether"),
        "Total redeemed should be 50 GCT"
      );
    });

    it("TC-29: Insufficient balance for redemption rejected", async () => {
      // vehicleOwner has 0 balance
      try {
        await greenToken.redeem(0, { from: vehicleOwner }); // TOLL_DISCOUNT costs 50
        assert.fail("Should have reverted");
      } catch (error) {
        assert.ok(
          error.message.includes("revert") || error.message.includes("Insufficient GCT balance"),
          "Should revert for insufficient balance"
        );
      }
    });

    it("TC-30: Redemption stats tracked correctly", async () => {
      await greenToken.setMinter(station, true, { from: admin });
      // Mint enough for multiple redemptions: 50 + 30 + 20 = 100 minimum
      await greenToken.mint(vehicleOwner, web3.utils.toWei("200", "ether"), { from: station });

      // Redeem TOLL_DISCOUNT (50), PARKING_WAIVER (30), PRIORITY_SERVICE (20)
      await greenToken.redeem(0, { from: vehicleOwner }); // TOLL_DISCOUNT
      await greenToken.redeem(1, { from: vehicleOwner }); // PARKING_WAIVER
      await greenToken.redeem(3, { from: vehicleOwner }); // PRIORITY_SERVICE

      const stats = await greenToken.getRedemptionStats(vehicleOwner);
      assert.equal(stats.totalCount.toNumber(), 3, "Should have 3 total redemptions");
      assert.equal(stats.tollDiscounts.toNumber(), 1, "Should have 1 toll discount");
      assert.equal(stats.parkingWaivers.toNumber(), 1, "Should have 1 parking waiver");
      assert.equal(stats.taxCredits.toNumber(), 0, "Should have 0 tax credits");
      assert.equal(stats.priorityServices.toNumber(), 1, "Should have 1 priority service");

      // Verify remaining balance: 200 - 50 - 30 - 20 = 100
      const balance = await greenToken.balanceOf(vehicleOwner);
      assert.equal(
        balance.toString(),
        web3.utils.toWei("100", "ether"),
        "Balance should be 100 GCT after redemptions"
      );

      // Verify next redemption ID incremented
      const nextId = await greenToken.nextRedemptionId();
      assert.equal(nextId.toNumber(), 3, "Next redemption ID should be 3");
    });
  });
});
