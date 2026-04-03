/**
 * Smart PUC — Truffle Unit Tests for EmissionContract
 * =====================================================
 * Test cases TC-01 through TC-05 (TC-06 is Sepolia-specific, manual).
 */

const EmissionContract = artifacts.require("EmissionContract");

contract("EmissionContract", (accounts) => {
    const owner = accounts[0];
    const nonOwner = accounts[1];
    let instance;

    beforeEach(async () => {
        instance = await EmissionContract.new({ from: owner });
    });

    // TC-01: Deploy and verify owner
    it("TC-01: should set deployer as owner", async () => {
        const contractOwner = await instance.owner();
        assert.equal(contractOwner, owner, "Owner should be the deployer");
    });

    // TC-01 (cont): verify default threshold
    it("TC-01: should have default threshold of 120", async () => {
        const threshold = await instance.threshold();
        assert.equal(threshold.toNumber(), 120, "Default threshold should be 120 g/km");
    });

    // TC-02: Store emission below threshold → PASS
    it("TC-02: should store PASS for co2=100 (below 120 threshold)", async () => {
        const vehicleId = "MH12AB1234";
        const co2 = 100;
        const timestamp = Math.floor(Date.now() / 1000);

        const tx = await instance.storeEmission(vehicleId, co2, timestamp, { from: owner });

        // Check RecordStored event
        assert.equal(tx.logs.length, 1, "Should emit exactly 1 event (RecordStored)");
        assert.equal(tx.logs[0].event, "RecordStored", "Event should be RecordStored");

        // Verify the record
        const record = await instance.getRecord(vehicleId, 0);
        assert.equal(record.vehicleId, vehicleId, "Vehicle ID mismatch");
        assert.equal(record.co2Level.toNumber(), co2, "CO2 mismatch");
        assert.equal(record.status, true, "Status should be PASS (true)");
    });

    // TC-03: Store emission above threshold → FAIL + ViolationDetected
    it("TC-03: should store FAIL and emit ViolationDetected for co2=150", async () => {
        const vehicleId = "MH14CD5678";
        const co2 = 150;
        const timestamp = Math.floor(Date.now() / 1000);

        const tx = await instance.storeEmission(vehicleId, co2, timestamp, { from: owner });

        // Should emit 2 events: RecordStored + ViolationDetected
        const events = tx.logs.map(l => l.event);
        assert.include(events, "RecordStored", "Should emit RecordStored");
        assert.include(events, "ViolationDetected", "Should emit ViolationDetected");

        // Verify FAIL status
        const record = await instance.getRecord(vehicleId, 0);
        assert.equal(record.status, false, "Status should be FAIL (false)");
        assert.equal(record.co2Level.toNumber(), 150, "CO2 should be 150");
    });

    // TC-04: Store 3 records and verify getRecord returns all correctly
    it("TC-04: should store and retrieve 3 records correctly", async () => {
        const vehicleId = "KA01EF9012";
        const values = [90, 130, 110];
        const baseTs = Math.floor(Date.now() / 1000);

        for (let i = 0; i < values.length; i++) {
            await instance.storeEmission(vehicleId, values[i], baseTs + i * 60, { from: owner });
        }

        // Verify count
        const count = await instance.getRecordCount(vehicleId);
        assert.equal(count.toNumber(), 3, "Should have 3 records");

        // Verify each record
        for (let i = 0; i < values.length; i++) {
            const rec = await instance.getRecord(vehicleId, i);
            assert.equal(rec.co2Level.toNumber(), values[i], `Record ${i} CO2 mismatch`);
            assert.equal(rec.status, values[i] <= 120, `Record ${i} status mismatch`);
        }

        // Verify getViolations returns only the FAIL (130)
        const violations = await instance.getViolations(vehicleId);
        assert.equal(violations.length, 1, "Should have 1 violation");
        assert.equal(violations[0].co2Level.toNumber(), 130, "Violation CO2 should be 130");
    });

    // TC-05: setThreshold from non-owner should revert
    it("TC-05: should revert setThreshold from non-owner", async () => {
        try {
            await instance.setThreshold(100, { from: nonOwner });
            assert.fail("Should have reverted");
        } catch (err) {
            assert.include(
                err.message,
                "Only contract owner",
                "Should revert with onlyOwner error"
            );
        }
    });

    // TC-05 (cont): owner CAN set threshold
    it("TC-05: owner should be able to set threshold", async () => {
        await instance.setThreshold(100, { from: owner });
        const newThreshold = await instance.threshold();
        assert.equal(newThreshold.toNumber(), 100, "Threshold should be updated to 100");
    });

    // Additional: input validation
    it("should reject empty vehicle ID", async () => {
        try {
            await instance.storeEmission("", 100, Date.now(), { from: owner });
            assert.fail("Should have reverted");
        } catch (err) {
            assert.include(err.message, "Vehicle ID cannot be empty");
        }
    });

    it("should reject zero CO2 value", async () => {
        try {
            await instance.storeEmission("TEST001", 0, Date.now(), { from: owner });
            assert.fail("Should have reverted");
        } catch (err) {
            assert.include(err.message, "CO2 value must be greater than zero");
        }
    });

    // TC-06 note: Sepolia deployment test is manual
    // Deploy to Sepolia → call storeEmission → verify on Etherscan
});
