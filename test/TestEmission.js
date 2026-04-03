/**
 * Smart PUC — Truffle Unit Tests for Upgraded EmissionContract
 * ==============================================================
 * Tests multi-pollutant storage, CES compliance, fraud detection,
 * vehicle stats, and per-pollutant violation events.
 */

const EmissionContract = artifacts.require("EmissionContract");

contract("EmissionContract", (accounts) => {
    const owner = accounts[0];
    const nonOwner = accounts[1];
    let instance;

    // Scaling factors matching the contract
    const SCALE_3 = 1000;
    const SCALE_4 = 10000;
    const SCALE_5 = 100000;

    // Helper to store a standard emission record
    async function storeRecord(vehicleId, co2, co, nox, hc, pm25, ces, fraud, vsp, phase, ts) {
        return instance.storeEmission(
            vehicleId,
            co2 || 100000,   // 100.0 g/km scaled
            co || 500,       // 0.5 g/km scaled
            nox || 30,       // 0.03 g/km scaled
            hc || 50,        // 0.05 g/km scaled
            pm25 || 200,     // 0.002 g/km scaled (×100000)
            ces || 7000,     // 0.70 CES scaled
            fraud || 1000,   // 0.10 fraud scaled
            vsp || 10000,    // 10.0 W/kg scaled
            phase || 0,      // Low phase
            ts || Math.floor(Date.now() / 1000),
            { from: owner }
        );
    }

    beforeEach(async () => {
        instance = await EmissionContract.new({ from: owner });
    });

    // TC-01: Deploy and verify owner
    it("TC-01: should set deployer as owner", async () => {
        const contractOwner = await instance.owner();
        assert.equal(contractOwner, owner, "Owner should be the deployer");
    });

    // TC-02: Store emission with PASS status (CES < 10000 and fraud < 6500)
    it("TC-02: should store PASS for CES=0.70 and fraud=0.10", async () => {
        const tx = await storeRecord("MH12AB1234");

        // Should emit RecordStored
        const events = tx.logs.map(l => l.event);
        assert.include(events, "RecordStored");

        // Verify record
        const record = await instance.getRecord("MH12AB1234", 0);
        assert.equal(record.vehicleId, "MH12AB1234");
        assert.equal(record.co2Level.toNumber(), 100000);
        assert.equal(record.status, true, "Should be PASS");
    });

    // TC-03: Store emission with FAIL status (CES >= 10000)
    it("TC-03: should store FAIL when CES >= 1.0", async () => {
        const tx = await storeRecord(
            "MH14CD5678",
            150000,  // 150 g/km CO2
            1200,    // 1.2 g/km CO
            80,      // 0.08 g/km NOx
            120,     // 0.12 g/km HC
            500,     // 0.005 g/km PM2.5
            12000,   // CES = 1.2 (FAIL)
            1000,    // fraud = 0.10
            15000, 2, Math.floor(Date.now() / 1000)
        );

        const events = tx.logs.map(l => l.event);
        assert.include(events, "ViolationDetected");

        const record = await instance.getRecord("MH14CD5678", 0);
        assert.equal(record.status, false, "Should be FAIL");
    });

    // TC-04: Fraud detection (fraud >= 0.65)
    it("TC-04: should emit FraudDetected when fraud score >= 0.65", async () => {
        const tx = await storeRecord(
            "KA01EF9012",
            100000, 500, 30, 50, 200,
            7000,    // CES = 0.70
            7500,    // fraud = 0.75 (above threshold)
            10000, 0, Math.floor(Date.now() / 1000)
        );

        const events = tx.logs.map(l => l.event);
        assert.include(events, "FraudDetected");
    });

    // TC-05: Multiple records and vehicle stats
    it("TC-05: should track vehicle stats correctly", async () => {
        const vehicleId = "KA01EF9012";
        const baseTs = Math.floor(Date.now() / 1000);

        // PASS record
        await storeRecord(vehicleId, 100000, 500, 30, 50, 200, 7000, 1000, 10000, 0, baseTs);
        // FAIL record (high CES)
        await storeRecord(vehicleId, 150000, 1200, 80, 120, 500, 12000, 1000, 15000, 2, baseTs + 60);
        // Fraud record
        await storeRecord(vehicleId, 100000, 500, 30, 50, 200, 7000, 8000, 10000, 0, baseTs + 120);

        const count = await instance.getRecordCount(vehicleId);
        assert.equal(count.toNumber(), 3);

        const stats = await instance.getVehicleStats(vehicleId);
        assert.equal(stats[0].toNumber(), 3, "Total records should be 3");
        assert.equal(stats[1].toNumber(), 1, "Violations should be 1");
        assert.equal(stats[2].toNumber(), 1, "Fraud alerts should be 1");
    });

    // TC-06: Violations filter
    it("TC-06: getViolations should return only FAIL records", async () => {
        const vehicleId = "DL01XY0001";
        const ts = Math.floor(Date.now() / 1000);

        await storeRecord(vehicleId, 100000, 500, 30, 50, 200, 7000, 1000, 10000, 0, ts);
        await storeRecord(vehicleId, 150000, 1200, 80, 120, 500, 12000, 1000, 15000, 2, ts + 60);

        const violations = await instance.getViolations(vehicleId);
        assert.equal(violations.length, 1);
        assert.equal(violations[0].status, false);
    });

    // TC-07: setThreshold from non-owner should revert
    it("TC-07: should revert setThreshold from non-owner", async () => {
        try {
            await instance.setThreshold(100000, { from: nonOwner });
            assert.fail("Should have reverted");
        } catch (err) {
            assert.include(err.message, "Only contract owner");
        }
    });

    // TC-08: Vehicle auto-registration
    it("TC-08: should auto-register vehicles", async () => {
        await storeRecord("MH12AB1234");
        await storeRecord("DL01XY0001");

        const vehicles = await instance.getRegisteredVehicles();
        assert.equal(vehicles.length, 2);
        assert.include(vehicles, "MH12AB1234");
        assert.include(vehicles, "DL01XY0001");
    });

    // TC-09: Input validation
    it("TC-09: should reject empty vehicle ID", async () => {
        try {
            await instance.storeEmission("", 100000, 500, 30, 50, 200, 7000, 1000, 10000, 0, Date.now(), { from: owner });
            assert.fail("Should have reverted");
        } catch (err) {
            assert.include(err.message, "Vehicle ID cannot be empty");
        }
    });

    // TC-10: All records retrieval
    it("TC-10: getAllRecords should return complete multi-pollutant data", async () => {
        await storeRecord("MH12AB1234", 115000, 800, 45, 75, 350, 8500, 2000, 12000, 1, Math.floor(Date.now() / 1000));

        const records = await instance.getAllRecords("MH12AB1234");
        assert.equal(records.length, 1);
        assert.equal(records[0].co2Level.toNumber(), 115000);
        assert.equal(records[0].coLevel.toNumber(), 800);
        assert.equal(records[0].noxLevel.toNumber(), 45);
        assert.equal(records[0].hcLevel.toNumber(), 75);
        assert.equal(records[0].pm25Level.toNumber(), 350);
        assert.equal(records[0].cesScore.toNumber(), 8500);
        assert.equal(records[0].wltcPhase, 1);
    });
});
