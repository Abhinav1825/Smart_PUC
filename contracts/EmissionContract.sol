// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

/**
 * @title EmissionContract
 * @author Smart PUC Team
 * @notice Blockchain-based Real-Time Vehicle Emission Monitoring & Compliance System.
 * @dev Stores per-vehicle emission records with 5 pollutants (CO2, CO, NOx, HC, PM2.5),
 *      CES score, fraud score, VSP value, and WLTC phase. Auto-evaluates PASS/FAIL
 *      against Bharat Stage VI thresholds and fraud detection logic.
 *
 *      Scaling conventions used throughout:
 *        - Pollutant values: scaled x1000  (e.g., 120.5 g/km => 120500)
 *        - CES / Fraud scores: scaled x10000 (e.g., 0.85 => 8500)
 *        - VSP value: scaled x1000
 */
contract EmissionContract {

    // ───────────────────────── State Variables ─────────────────────────

    /// @notice Contract owner (deployer) — only owner can update thresholds
    address public owner;

    /// @notice Configurable CO2 compliance threshold (scaled x1000, default 120000 = 120 g/km)
    uint256 public threshold;

    // ───────────────────── BSVI Threshold Constants ───────────────────
    // All pollutant thresholds are scaled x1000

    /// @notice BSVI CO2 threshold: 120 g/km => 120000
    uint256 public constant BSVI_CO2  = 120000;

    /// @notice BSVI CO threshold: 1.0 g/km => 1000
    uint256 public constant BSVI_CO   = 1000;

    /// @notice BSVI NOx threshold: 0.060 g/km => 60
    uint256 public constant BSVI_NOX  = 60;

    /// @notice BSVI HC threshold: 0.100 g/km => 100
    uint256 public constant BSVI_HC   = 100;

    /// @notice BSVI PM2.5 threshold: 0.004 g/km => 4
    uint256 public constant BSVI_PM25 = 4;

    /// @notice CES score ceiling for compliance (scaled x10000). Must be < 10000 (i.e., < 1.0)
    uint256 public constant CES_PASS_CEILING = 10000;

    /// @notice Fraud score threshold (scaled x10000). >= 6500 (i.e., >= 0.65) triggers fraud alert
    uint256 public constant FRAUD_ALERT_THRESHOLD = 6500;

    // ───────────────────────── Structs ─────────────────────────────────

    /**
     * @notice Represents a single emission measurement for a vehicle.
     * @param vehicleId  Vehicle registration number (e.g., "MH12AB1234")
     * @param co2        CO2 emission (scaled x1000)
     * @param co         CO emission (scaled x1000)
     * @param nox        NOx emission (scaled x1000)
     * @param hc         HC emission (scaled x1000)
     * @param pm25       PM2.5 emission (scaled x1000)
     * @param cesScore   Composite Emission Score (scaled x10000)
     * @param fraudScore Fraud detection score (scaled x10000)
     * @param vspValue   Vehicle Specific Power (scaled x1000)
     * @param wltcPhase  WLTC driving phase: 0=Low, 1=Med, 2=High, 3=ExtraHigh
     * @param timestamp  Unix epoch seconds of the reading
     * @param status     true = PASS, false = FAIL
     */
    struct EmissionRecord {
        string  vehicleId;
        uint256 co2;
        uint256 co;
        uint256 nox;
        uint256 hc;
        uint256 pm25;
        uint256 cesScore;
        uint256 fraudScore;
        uint256 vspValue;
        uint8   wltcPhase;
        uint256 timestamp;
        bool    status;
    }

    // ───────────────────────── Mappings ────────────────────────────────

    /// @notice Per-vehicle emission history: vehicleId => EmissionRecord[]
    mapping(string => EmissionRecord[]) private emissionRecords;

    /// @notice Master list of all vehicle IDs that have at least one record
    string[] private registeredVehicles;

    /// @notice Quick lookup: vehicleId => already registered?
    mapping(string => bool) private isRegistered;

    /// @notice Number of FAIL records per vehicle
    mapping(string => uint256) public violationCount;

    /// @notice Number of fraud alerts (fraudScore >= 6500) per vehicle
    mapping(string => uint256) public fraudAlertCount;

    /// @notice Running sum of CES scores per vehicle (for computing average)
    mapping(string => uint256) private cesSumByVehicle;

    // ───────────────────────── Events ──────────────────────────────────

    /// @notice Emitted on every successful record storage
    event RecordStored(
        string  indexed vehicleId,
        uint256 recordIndex,
        uint256 timestamp
    );

    /// @notice Emitted on every FAIL outcome
    event ViolationDetected(
        string  indexed vehicleId,
        uint256 cesScore,
        uint256 timestamp
    );

    /// @notice Emitted when fraudScore >= FRAUD_ALERT_THRESHOLD
    event FraudDetected(
        string  indexed vehicleId,
        uint256 fraudScore,
        uint256 timestamp
    );

    /// @notice Emitted when an individual pollutant exceeds its BSVI threshold
    event PollutantViolation(
        string  vehicleId,
        string  pollutant,
        uint256 value,
        uint256 threshold_
    );

    // ───────────────────────── Modifiers ───────────────────────────────

    /// @notice Restricts function access to the contract owner
    modifier onlyOwner() {
        require(msg.sender == owner, "Only contract owner can call this function");
        _;
    }

    // ───────────────────────── Constructor ─────────────────────────────

    /**
     * @notice Deploys the contract, sets caller as owner, and initialises
     *         the CO2 threshold to the BSVI default (120000 = 120 g/km scaled x1000).
     */
    constructor() {
        owner = msg.sender;
        threshold = BSVI_CO2;
    }

    // ───────────────────────── Core Functions ──────────────────────────

    /**
     * @notice Store a new emission record for a vehicle.
     * @param _vehicleId  Registration number (e.g., "MH12AB1234")
     * @param _co2        CO2 emission value (scaled x1000)
     * @param _co         CO emission value (scaled x1000)
     * @param _nox        NOx emission value (scaled x1000)
     * @param _hc         HC emission value (scaled x1000)
     * @param _pm25       PM2.5 emission value (scaled x1000)
     * @param _cesScore   Composite Emission Score (scaled x10000)
     * @param _fraudScore Fraud detection score (scaled x10000)
     * @param _vspValue   Vehicle Specific Power (scaled x1000)
     * @param _wltcPhase  WLTC phase: 0=Low, 1=Med, 2=High, 3=ExtraHigh
     * @param _timestamp  Unix epoch timestamp of the reading
     *
     * @dev Compliance logic: PASS requires cesScore < 10000 AND fraudScore < 6500.
     *      Individual pollutant violations emit PollutantViolation events.
     *      Fraud alerts emit FraudDetected events.
     */
    function storeEmission(
        string memory _vehicleId,
        uint256 _co2,
        uint256 _co,
        uint256 _nox,
        uint256 _hc,
        uint256 _pm25,
        uint256 _cesScore,
        uint256 _fraudScore,
        uint256 _vspValue,
        uint8   _wltcPhase,
        uint256 _timestamp
    ) public {
        // Input validation
        require(bytes(_vehicleId).length > 0, "Vehicle ID cannot be empty");
        require(_timestamp > 0, "Timestamp must be greater than zero");
        require(_wltcPhase <= 3, "Invalid WLTC phase (0-3)");

        // Compliance check: PASS requires cesScore < CES_PASS_CEILING AND fraudScore < FRAUD_ALERT_THRESHOLD
        bool passed = (_cesScore < CES_PASS_CEILING) && (_fraudScore < FRAUD_ALERT_THRESHOLD);

        // Create and store the record
        EmissionRecord memory record = EmissionRecord({
            vehicleId:  _vehicleId,
            co2:        _co2,
            co:         _co,
            nox:        _nox,
            hc:         _hc,
            pm25:       _pm25,
            cesScore:   _cesScore,
            fraudScore: _fraudScore,
            vspValue:   _vspValue,
            wltcPhase:  _wltcPhase,
            timestamp:  _timestamp,
            status:     passed
        });

        emissionRecords[_vehicleId].push(record);
        uint256 recordIndex = emissionRecords[_vehicleId].length - 1;

        // Update CES running sum for average calculation
        cesSumByVehicle[_vehicleId] += _cesScore;

        // Auto-register vehicle if first record
        if (!isRegistered[_vehicleId]) {
            registeredVehicles.push(_vehicleId);
            isRegistered[_vehicleId] = true;
        }

        // Emit record stored event
        emit RecordStored(_vehicleId, recordIndex, _timestamp);

        // Check individual pollutant thresholds and emit violations
        if (_co2 > threshold) {
            emit PollutantViolation(_vehicleId, "CO2", _co2, threshold);
        }
        if (_co > BSVI_CO) {
            emit PollutantViolation(_vehicleId, "CO", _co, BSVI_CO);
        }
        if (_nox > BSVI_NOX) {
            emit PollutantViolation(_vehicleId, "NOx", _nox, BSVI_NOX);
        }
        if (_hc > BSVI_HC) {
            emit PollutantViolation(_vehicleId, "HC", _hc, BSVI_HC);
        }
        if (_pm25 > BSVI_PM25) {
            emit PollutantViolation(_vehicleId, "PM25", _pm25, BSVI_PM25);
        }

        // Track fraud alerts
        if (_fraudScore >= FRAUD_ALERT_THRESHOLD) {
            fraudAlertCount[_vehicleId]++;
            emit FraudDetected(_vehicleId, _fraudScore, _timestamp);
        }

        // Track violations
        if (!passed) {
            violationCount[_vehicleId]++;
            emit ViolationDetected(_vehicleId, _cesScore, _timestamp);
        }
    }

    // ───────────────────────── View Functions ──────────────────────────

    /**
     * @notice Get aggregated statistics for a vehicle.
     * @param _vehicleId Vehicle registration number
     * @return totalRecords  Total number of emission records
     * @return violations    Number of FAIL records
     * @return fraudAlerts   Number of fraud alert records
     * @return averageCES    Average CES score (scaled x10000), 0 if no records
     */
    function getVehicleStats(
        string memory _vehicleId
    ) public view returns (
        uint256 totalRecords,
        uint256 violations,
        uint256 fraudAlerts,
        uint256 averageCES
    ) {
        totalRecords = emissionRecords[_vehicleId].length;
        violations = violationCount[_vehicleId];
        fraudAlerts = fraudAlertCount[_vehicleId];
        averageCES = totalRecords > 0 ? cesSumByVehicle[_vehicleId] / totalRecords : 0;
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
        uint256 vCount = 0;
        for (uint256 i = 0; i < records.length; i++) {
            if (!records[i].status) {
                vCount++;
            }
        }

        // Second pass: collect violations
        EmissionRecord[] memory violations = new EmissionRecord[](vCount);
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
     * @notice Update the CO2 compliance threshold. Owner-only.
     * @param _threshold New CO2 threshold (scaled x1000, must be > 0)
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
