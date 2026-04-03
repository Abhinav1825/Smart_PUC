// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

/**
 * @title EmissionContract
 * @notice Blockchain-based Real-Time Vehicle Emission Monitoring & Compliance System (Smart PUC)
 * @dev Stores per-vehicle emission records, auto-evaluates PASS/FAIL against a configurable
 *      CO₂ threshold (default 120 g/km, aligned with Bharat Stage VI / EURO 6 for petrol vehicles).
 */
contract EmissionContract {

    // ───────────────────────── State Variables ─────────────────────────

    /// @notice Contract owner (deployer) — only owner can update the threshold
    address public owner;

    /// @notice CO₂ compliance threshold in g/km (default 120)
    uint256 public threshold;

    /// @notice Per-vehicle emission history:  vehicleId → EmissionRecord[]
    mapping(string => EmissionRecord[]) private emissionRecords;

    /// @notice Master list of all vehicle IDs that have at least one record
    string[] private registeredVehicles;

    /// @notice Quick lookup: vehicleId → already registered?
    mapping(string => bool) private isRegistered;

    // ───────────────────────── Structs ─────────────────────────────────

    struct EmissionRecord {
        string  vehicleId;
        uint256 co2Level;       // g/km
        uint256 timestamp;      // Unix epoch seconds
        bool    status;         // true = PASS, false = FAIL
    }

    // ───────────────────────── Events ──────────────────────────────────

    /// @notice Emitted on every FAIL outcome
    event ViolationDetected(
        string  indexed vehicleId,
        uint256 co2Level,
        uint256 timestamp
    );

    /// @notice Emitted on every successful record storage
    event RecordStored(
        string  indexed vehicleId,
        uint256 recordIndex,
        uint256 timestamp
    );

    // ───────────────────────── Modifiers ───────────────────────────────

    modifier onlyOwner() {
        require(msg.sender == owner, "Only contract owner can call this function");
        _;
    }

    // ───────────────────────── Constructor ─────────────────────────────

    /**
     * @notice Deploys the contract and sets the caller as the owner.
     *         Default CO₂ threshold is 120 g/km.
     */
    constructor() {
        owner = msg.sender;
        threshold = 120; // g/km — Bharat Stage VI aligned for petrol
    }

    // ───────────────────────── Core Functions ──────────────────────────

    /**
     * @notice Store a new emission record for a vehicle.
     * @param _vehicleId  Registration number (e.g., "MH12AB1234")
     * @param _co2        CO₂ emission value in g/km (must be > 0)
     * @param _timestamp  Unix epoch timestamp of the reading
     *
     * The function auto-evaluates compliance (PASS if co2 <= threshold, FAIL otherwise).
     * If FAIL, the ViolationDetected event is emitted.
     * RecordStored event is emitted on every call.
     */
    function storeEmission(
        string memory _vehicleId,
        uint256 _co2,
        uint256 _timestamp
    ) public {
        // Input validation (NFR-04)
        require(bytes(_vehicleId).length > 0, "Vehicle ID cannot be empty");
        require(_co2 > 0, "CO2 value must be greater than zero");
        require(_timestamp > 0, "Timestamp must be greater than zero");

        // Auto compliance check
        bool passed = checkCompliance(_co2);

        // Create and store the record
        EmissionRecord memory record = EmissionRecord({
            vehicleId: _vehicleId,
            co2Level:  _co2,
            timestamp: _timestamp,
            status:    passed
        });

        emissionRecords[_vehicleId].push(record);
        uint256 recordIndex = emissionRecords[_vehicleId].length - 1;

        // Track vehicle registration
        if (!isRegistered[_vehicleId]) {
            registeredVehicles.push(_vehicleId);
            isRegistered[_vehicleId] = true;
        }

        // Emit events
        emit RecordStored(_vehicleId, recordIndex, _timestamp);

        if (!passed) {
            emit ViolationDetected(_vehicleId, _co2, _timestamp);
        }
    }

    /**
     * @notice Internal compliance evaluation.
     * @param _co2 CO₂ value in g/km
     * @return true if within threshold (PASS), false otherwise (FAIL)
     */
    function checkCompliance(uint256 _co2) internal view returns (bool) {
        return _co2 <= threshold;
    }

    /**
     * @notice Retrieve a single emission record by vehicle ID and index.
     * @param _vehicleId  Vehicle registration number
     * @param _index      0-based index in the vehicle's record array
     * @return The EmissionRecord struct at the given index
     */
    function getRecord(
        string memory _vehicleId,
        uint256 _index
    ) public view returns (EmissionRecord memory) {
        require(_index < emissionRecords[_vehicleId].length, "Record index out of bounds");
        return emissionRecords[_vehicleId][_index];
    }

    /**
     * @notice Get the total number of records for a vehicle.
     * @param _vehicleId Vehicle registration number
     * @return count Number of emission records stored
     */
    function getRecordCount(
        string memory _vehicleId
    ) public view returns (uint256 count) {
        return emissionRecords[_vehicleId].length;
    }

    /**
     * @notice Get all records for a vehicle.
     * @param _vehicleId Vehicle registration number
     * @return Array of EmissionRecord structs
     */
    function getAllRecords(
        string memory _vehicleId
    ) public view returns (EmissionRecord[] memory) {
        return emissionRecords[_vehicleId];
    }

    /**
     * @notice Get all FAIL records for a vehicle.
     * @param _vehicleId Vehicle registration number
     * @return Array of EmissionRecord structs where status == false (FAIL)
     */
    function getViolations(
        string memory _vehicleId
    ) public view returns (EmissionRecord[] memory) {
        EmissionRecord[] storage records = emissionRecords[_vehicleId];
        
        // First pass: count violations
        uint256 violationCount = 0;
        for (uint256 i = 0; i < records.length; i++) {
            if (!records[i].status) {
                violationCount++;
            }
        }

        // Second pass: collect violations
        EmissionRecord[] memory violations = new EmissionRecord[](violationCount);
        uint256 j = 0;
        for (uint256 i = 0; i < records.length; i++) {
            if (!records[i].status) {
                violations[j] = records[i];
                j++;
            }
        }

        return violations;
    }

    /**
     * @notice Update the CO₂ compliance threshold. Owner-only.
     * @param _threshold New threshold in g/km (must be > 0)
     */
    function setThreshold(uint256 _threshold) public onlyOwner {
        require(_threshold > 0, "Threshold must be greater than zero");
        threshold = _threshold;
    }

    /**
     * @notice Get list of all registered vehicle IDs.
     * @return Array of vehicle ID strings
     */
    function getRegisteredVehicles() public view returns (string[] memory) {
        return registeredVehicles;
    }
}
