const EmissionContract = artifacts.require("EmissionContract");

contract("EmissionContract", (accounts) => {
  const [owner, nonOwner, authorizedCaller] = accounts;
  let instance;

  beforeEach(async () => {
    instance = await EmissionContract.new({ from: owner });
  });

  // TC-01: Owner is deployer, threshold is 120000
  it("TC-01: should set deployer as owner with CO2 threshold 120000", async () => {
    const contractOwner = await instance.owner();
    assert.equal(contractOwner, owner, "Owner should be deployer");

    const threshold = await instance.threshold();
    assert.equal(threshold.toNumber(), 120000, "CO2 threshold should be 120000");
  });

  // TC-02: Store PASS record and verify RecordStored event + fields
  it("TC-02: should store a PASS record and emit RecordStored", async () => {
    const tx = await instance.storeEmission(
      "MH12AB1234",  // vehicleId
      100000,         // co2 (100.000 g/km)
      500,            // co  (0.500 g/km)
      30,             // nox (0.030 g/km)
      50,             // hc  (0.050 g/km)
      2,              // pm25 (0.002 g/km)
      7000,           // cesScore (0.7000)
      1000,           // fraudScore (0.1000)
      15000,          // vspValue
      2,              // wltcPhase
      1700000000,     // timestamp
      { from: owner }
    );

    // Verify RecordStored event
    assert.equal(tx.logs.length > 0, true, "Should emit at least one event");
    const event = tx.logs.find((log) => log.event === "RecordStored");
    assert.ok(event, "RecordStored event should be emitted");
    assert.equal(event.args.vehicleId, "MH12AB1234");

    // Verify stored record
    const record = await instance.getRecord("MH12AB1234", 0);
    assert.equal(record.co2.toNumber(), 100000);
    assert.equal(record.co.toNumber(), 500);
    assert.equal(record.status, true, "Record should PASS");
  });

  // TC-03: Store FAIL record (CES >= 10000) and verify ViolationDetected
  it("TC-03: should emit ViolationDetected for high CES score", async () => {
    const tx = await instance.storeEmission(
      "MH12CD5678",
      130000,   // co2 above threshold
      1200,     // co above threshold
      80,       // nox above threshold
      120,      // hc above threshold
      8,        // pm25 above threshold
      12000,    // cesScore >= 10000 -> FAIL
      2000,     // fraudScore
      20000,    // vspValue
      3,        // wltcPhase
      1700000100,
      { from: owner }
    );

    const event = tx.logs.find((log) => log.event === "ViolationDetected");
    assert.ok(event, "ViolationDetected event should be emitted");
    assert.equal(event.args.vehicleId, "MH12CD5678");
  });

  // TC-04: Fraud detection (fraudScore >= 6500)
  it("TC-04: should emit FraudDetected for high fraud score", async () => {
    const tx = await instance.storeEmission(
      "MH12EF9012",
      90000,
      400,
      20,
      40,
      1,
      5000,    // cesScore OK
      7500,    // fraudScore >= 6500 -> fraud
      12000,
      1,
      1700000200,
      { from: owner }
    );

    const event = tx.logs.find((log) => log.event === "FraudDetected");
    assert.ok(event, "FraudDetected event should be emitted");
    assert.equal(event.args.vehicleId, "MH12EF9012");
  });

  // TC-05: Multiple records + getVehicleStats
  it("TC-05: should track multiple records and return correct stats", async () => {
    await instance.storeEmission(
      "MH12GH3456", 95000, 400, 25, 40, 2, 6000, 1000, 14000, 2, 1700000300,
      { from: owner }
    );
    await instance.storeEmission(
      "MH12GH3456", 110000, 800, 50, 80, 4, 8500, 2000, 18000, 3, 1700000400,
      { from: owner }
    );
    await instance.storeEmission(
      "MH12GH3456", 125000, 1100, 70, 110, 6, 11000, 3000, 22000, 3, 1700000500,
      { from: owner }
    );

    const stats = await instance.getVehicleStats("MH12GH3456");
    assert.equal(stats.totalRecords.toNumber(), 3, "Should have 3 records");
  });

  // TC-06: getViolations returns only FAIL records
  it("TC-06: should return only FAIL records from getViolations", async () => {
    // PASS record
    await instance.storeEmission(
      "MH12IJ7890", 90000, 400, 20, 40, 1, 5000, 1000, 12000, 1, 1700000600,
      { from: owner }
    );
    // FAIL record
    await instance.storeEmission(
      "MH12IJ7890", 130000, 1200, 80, 120, 8, 12000, 2000, 25000, 3, 1700000700,
      { from: owner }
    );

    const violations = await instance.getViolations("MH12IJ7890");
    assert.equal(violations.length, 1, "Should have exactly 1 violation");
  });

  // TC-07: setThreshold reverts from non-owner
  it("TC-07: should revert setThreshold from non-owner", async () => {
    try {
      await instance.setThreshold(150000, { from: nonOwner });
      assert.fail("Should have reverted");
    } catch (error) {
      assert.ok(
        error.message.includes("revert"),
        "Should revert for non-owner"
      );
    }
  });

  // TC-08: Auto-registration of vehicles
  it("TC-08: should auto-register vehicles on first emission", async () => {
    await instance.storeEmission(
      "MH12KL2345", 95000, 500, 30, 50, 2, 7000, 1000, 15000, 2, 1700000800,
      { from: owner }
    );

    const recordCount = await instance.getRecordCount("MH12KL2345");
    assert.ok(recordCount.toNumber() > 0, "Vehicle should be auto-registered (record count > 0)");
  });

  // TC-09: Empty vehicle ID rejected
  it("TC-09: should reject empty vehicle ID", async () => {
    try {
      await instance.storeEmission(
        "", 95000, 500, 30, 50, 2, 7000, 1000, 15000, 2, 1700000900,
        { from: owner }
      );
      assert.fail("Should have reverted");
    } catch (error) {
      assert.ok(
        error.message.includes("revert"),
        "Should revert for empty vehicle ID"
      );
    }
  });

  // TC-10: All record fields stored correctly (verify all 12 fields)
  it("TC-10: should store all 12 fields correctly", async () => {
    await instance.storeEmission(
      "MH12MN6789",
      105000,   // co2
      600,      // co
      35,       // nox
      55,       // hc
      3,        // pm25
      7500,     // cesScore
      1500,     // fraudScore
      16000,    // vspValue
      2,        // wltcPhase
      1700001000, // timestamp
      { from: owner }
    );

    const record = await instance.getRecord("MH12MN6789", 0);
    assert.equal(record.vehicleId, "MH12MN6789", "vehicleId mismatch");
    assert.equal(record.co2.toNumber(), 105000, "co2 mismatch");
    assert.equal(record.co.toNumber(), 600, "co mismatch");
    assert.equal(record.nox.toNumber(), 35, "nox mismatch");
    assert.equal(record.hc.toNumber(), 55, "hc mismatch");
    assert.equal(record.pm25.toNumber(), 3, "pm25 mismatch");
    assert.equal(record.cesScore.toNumber(), 7500, "cesScore mismatch");
    assert.equal(record.fraudScore.toNumber(), 1500, "fraudScore mismatch");
    assert.equal(record.vspValue.toNumber(), 16000, "vspValue mismatch");
    assert.equal(record.wltcPhase.toNumber(), 2, "wltcPhase mismatch");
    assert.equal(record.timestamp.toNumber(), 1700001000, "timestamp mismatch");
    assert.equal(record.status, true, "status mismatch");
  });

  // TC-11: Unauthorized caller rejected
  it("TC-11: should reject storeEmission from unauthorized caller", async () => {
    try {
      await instance.storeEmission(
        "MH12OP1234", 95000, 500, 30, 50, 2, 7000, 1000, 15000, 2, 1700001100,
        { from: nonOwner }
      );
      assert.fail("Should have reverted");
    } catch (error) {
      assert.ok(
        error.message.includes("revert"),
        "Should revert for unauthorized caller"
      );
    }
  });

  // TC-12: Owner can authorize a new caller
  it("TC-12: should allow owner to authorize a new caller", async () => {
    // Authorize the new caller
    await instance.setAuthorizedCaller(authorizedCaller, true, { from: owner });

    // Authorized caller stores emission successfully
    const tx = await instance.storeEmission(
      "MH12QR5678", 98000, 450, 28, 45, 2, 6500, 900, 14000, 2, 1700001200,
      { from: authorizedCaller }
    );

    const event = tx.logs.find((log) => log.event === "RecordStored");
    assert.ok(event, "RecordStored event should be emitted for authorized caller");
    assert.equal(event.args.vehicleId, "MH12QR5678");
  });
});
