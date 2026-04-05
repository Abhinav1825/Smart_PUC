// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "@openzeppelin/contracts-upgradeable/proxy/utils/Initializable.sol";
import "@openzeppelin/contracts-upgradeable/proxy/utils/UUPSUpgradeable.sol";
import "@openzeppelin/contracts-upgradeable/security/ReentrancyGuardUpgradeable.sol";
import "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";

/**
 * @title EmissionRegistry
 * @author Smart PUC Team
 * @notice Immutable registry of vehicle emission records with 3-node trust model.
 * @dev Implements role-based access (Admin, Testing Station, OBD Device) and
 *      cryptographic device signature verification to ensure data provenance.
 *
 *      3-Node Trust Model:
 *        1. OBD Device (registered) signs telemetry data with its private key
 *        2. Testing Station (authorized) validates and submits to blockchain
 *        3. Contract verifies both station authorization AND device signature
 *
 *      Scaling conventions:
 *        - Pollutant values (CO2, CO, NOx, HC, PM2.5): scaled x1000
 *        - CES / Fraud scores: scaled x10000
 *        - VSP value: scaled x1000
 *
 *      Improvements over v1:
 *        - On-chain CES calculation (no longer trusts station-supplied CES)
 *        - bytes32 vehicle ID hashing for gas-efficient internal mappings
 *        - Nonce-based replay protection
 *        - Optional soft cap on distinct vehicles (pilot mode)
 *        - O(1) violation index tracking with paginated retrieval
 */
/**
 * @dev Storage layout rules (critical for UUPS upgrades)
 * -----------------------------------------------------
 * Do NOT reorder, insert, or delete state variables between the marker
 * comments. New variables MUST be appended at the end of the existing
 * list, and any deprecation MUST keep the slot by leaving a placeholder.
 * The `__gap` at the bottom provides 50 reserved slots for future
 * upgrades.
 */
contract EmissionRegistry is
    Initializable,
    UUPSUpgradeable,
    ReentrancyGuardUpgradeable
{
    using ECDSA for bytes32;

    // ───────────────────────── Roles ──────────────────────────────────────

    /// @notice Contract administrator (deployer)
    address public admin;

    /// @notice Authorized testing stations that can submit emission records
    mapping(address => bool) public testingStations;

    /// @notice Registered OBD devices whose signatures are accepted
    mapping(address => bool) public registeredDevices;

    // ───────────────────────── Contract References ────────────────────────

    /// @notice Address of the PUCCertificate NFT contract
    address public pucCertificateContract;

    // ───────────────────── BSVI Threshold Constants ──────────────────────

    uint256 public constant BSVI_CO2  = 120000;   // 120 g/km x1000
    uint256 public constant BSVI_CO   = 1000;     // 1.0 g/km x1000
    uint256 public constant BSVI_NOX  = 60;       // 0.060 g/km x1000
    uint256 public constant BSVI_HC   = 100;      // 0.100 g/km x1000
    uint256 public constant BSVI_PM25 = 5;        // 0.0045 g/km x1000 (rounded up)
    uint256 public constant CES_PASS_CEILING = 10000;       // 1.0 x10000
    uint256 public constant FRAUD_ALERT_THRESHOLD = 6500;   // 0.65 x10000

    /// @notice Number of consecutive PASS readings required for certificate eligibility
    uint256 public constant CONSECUTIVE_PASS_REQUIRED = 3;

    // ───────────────── CES Weight Constants (scaled x10000) ──────────────

    uint256 private constant CES_WEIGHT_CO2  = 3500;
    uint256 private constant CES_WEIGHT_NOX  = 3000;
    uint256 private constant CES_WEIGHT_CO   = 1500;
    uint256 private constant CES_WEIGHT_HC   = 1200;
    uint256 private constant CES_WEIGHT_PM25 = 800;
    uint256 private constant CES_WEIGHT_TOTAL = 10000;

    // ───────────────────── Vehicle Tracking ──────────────────────────────
    //
    // v3.0 imposed a hard MAX_VEHICLES = 10_000 cap as a defensive bound.
    // That limit is too small for any real deployment (India has ~300M
    // vehicles) and paper reviewers correctly flagged it as a toy number.
    //
    // v3.1 removes the hard cap and replaces it with an *advisory* soft
    // cap. Nothing in the contract storage or algorithms grows unboundedly:
    //   * registeredVehicles[] is append-only and read via pagination, so
    //     storage cost is O(1) per write regardless of total size.
    //   * every mapping is keyed by bytes32 hash and has O(1) access.
    //   * violation/record arrays are per-vehicle and paginated, so a
    //     single vehicle cannot poison reads for others.
    //
    // Operators can set ``softVehicleCap`` to non-zero to rate-limit new
    // vehicle registrations per deployment (e.g. during a controlled pilot).
    // A value of zero disables the soft cap entirely — the default.

    uint256 public vehicleCount;

    /// @notice Advisory soft cap on distinct registered vehicles (0 = no cap).
    /// Used for controlled pilot deployments; set via ``setSoftVehicleCap``.
    uint256 public softVehicleCap;

    // ───────────────────────── Structs ────────────────────────────────────

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
        address deviceAddress;      // OBD device that signed the data
        address stationAddress;     // Testing station that submitted
    }

    // ───────────────────────── Storage ────────────────────────────────────

    // Internal mappings use bytes32 vehicle ID hash for gas efficiency
    mapping(bytes32 => EmissionRecord[]) private emissionRecords;
    string[] private registeredVehicles;
    mapping(bytes32 => bool) private isRegistered;

    /// @notice Number of FAIL records per vehicle
    mapping(bytes32 => uint256) private _violationCount;

    /// @notice Number of fraud alerts per vehicle
    mapping(bytes32 => uint256) private _fraudAlertCount;

    /// @notice Running CES sum for average calculation
    mapping(bytes32 => uint256) private cesSumByVehicle;

    /// @notice Consecutive PASS count for certificate eligibility
    mapping(bytes32 => uint256) private _consecutivePassCount;

    /// @notice Vehicle ID => owner wallet address (set when owner claims)
    mapping(bytes32 => address) private _vehicleOwners;

    /// @notice Indices of FAIL records for each vehicle (O(1) violation lookup)
    mapping(bytes32 => uint256[]) private violationIndices;

    /// @notice Replay protection: tracks used nonces
    mapping(bytes32 => bool) public usedNonces;

    // ───────────────────────── Events ─────────────────────────────────────

    event RecordStored(
        string  indexed vehicleId,
        uint256 recordIndex,
        uint256 timestamp,
        address indexed station,
        address indexed device
    );

    event ViolationDetected(
        string  indexed vehicleId,
        uint256 cesScore,
        uint256 timestamp
    );

    event FraudDetected(
        string  indexed vehicleId,
        uint256 fraudScore,
        uint256 timestamp
    );

    event PollutantViolation(
        string  vehicleId,
        string  pollutant,
        uint256 value,
        uint256 threshold_
    );

    event CertificateEligible(
        string  indexed vehicleId,
        uint256 consecutivePasses
    );

    event StationUpdated(address indexed station, bool authorized);
    event DeviceUpdated(address indexed device, bool registered);
    event VehicleOwnerSet(string indexed vehicleId, address indexed owner);
    event NonceUsed(bytes32 indexed nonce);

    // ───────────────────────── Modifiers ──────────────────────────────────

    modifier onlyAdmin() {
        require(msg.sender == admin, "Only admin can call this function");
        _;
    }

    modifier onlyStation() {
        require(testingStations[msg.sender], "Caller is not an authorized testing station");
        _;
    }

    // ───────────────────────── Storage Gap (UUPS) ────────────────────────
    //
    // Reserved slots for future upgrades. NEVER shrink this array — that
    // would collide with future state added by subclasses / upgrades.
    // Append new variables BEFORE the gap and shrink the gap by the
    // corresponding number of slots.
    uint256[50] private __gap;

    // ───────────────────────── Initializer (UUPS) ────────────────────────

    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }

    /**
     * @notice Initialize the upgradeable registry. Call exactly once via
     *         the proxy deployment; subsequent calls revert.
     */
    function initialize() external initializer {
        __UUPSUpgradeable_init();
        __ReentrancyGuard_init();
        admin = msg.sender;
        // Admin is also an authorized station for initial setup/testing.
        testingStations[msg.sender] = true;
    }

    // ───────────────────────── UUPS Upgrade Auth ─────────────────────────

    /// @dev Only the current admin may authorize an upgrade.
    function _authorizeUpgrade(address newImplementation) internal override onlyAdmin {
        // No-op; the onlyAdmin modifier is the access check.
    }

    // ───────────────────────── Internal Helpers ──────────────────────────

    /// @dev Hash a string vehicle ID to bytes32 for gas-efficient mapping keys
    function _vid(string memory _vehicleId) internal pure returns (bytes32) {
        return keccak256(bytes(_vehicleId));
    }

    /// @dev Compute CES on-chain from raw pollutant values (all scaled x1000).
    ///      CES = (co2/BSVI_CO2)*3500 + (nox/BSVI_NOX)*3000 + (co/BSVI_CO)*1500
    ///            + (hc/BSVI_HC)*1200 + (pm25/BSVI_PM25)*800, all divided by 10000.
    ///      Returns CES scaled x10000 to maintain precision.
    function _computeCES(
        uint256 _co2,
        uint256 _co,
        uint256 _nox,
        uint256 _hc,
        uint256 _pm25
    ) internal pure returns (uint256) {
        // Each term: (value * weight / threshold) gives a weighted ratio.
        // At exactly the BSVI threshold every term equals its weight, so
        // the sum equals CES_WEIGHT_TOTAL = 10000 = CES_PASS_CEILING. This
        // is already in the x10000 scale — no further multiplication needed.
        uint256 cesRaw = (_co2 * CES_WEIGHT_CO2 / BSVI_CO2)
                       + (_nox * CES_WEIGHT_NOX / BSVI_NOX)
                       + (_co * CES_WEIGHT_CO / BSVI_CO)
                       + (_hc * CES_WEIGHT_HC / BSVI_HC)
                       + (_pm25 * CES_WEIGHT_PM25 / BSVI_PM25);

        return cesRaw;
    }

    // ───────────────────────── Admin Functions ────────────────────────────

    /// @notice Register or deregister a testing station
    function setTestingStation(address _station, bool _authorized) external onlyAdmin {
        testingStations[_station] = _authorized;
        emit StationUpdated(_station, _authorized);
    }

    /// @notice Register or deregister an OBD device
    function setRegisteredDevice(address _device, bool _registered) external onlyAdmin {
        registeredDevices[_device] = _registered;
        emit DeviceUpdated(_device, _registered);
    }

    /// @notice Set the PUCCertificate contract address
    function setPUCCertificateContract(address _addr) external onlyAdmin {
        pucCertificateContract = _addr;
    }

    /// @notice Set an advisory soft cap on distinct registered vehicles.
    ///         Pass 0 to disable the cap entirely.
    /// @dev Intended for controlled pilot deployments; not a security feature.
    function setSoftVehicleCap(uint256 _cap) external onlyAdmin {
        softVehicleCap = _cap;
    }

    /// @notice Transfer admin role
    function transferAdmin(address _newAdmin) external onlyAdmin {
        require(_newAdmin != address(0), "Invalid admin address");
        admin = _newAdmin;
    }

    // ───────────────────────── Vehicle Owner ──────────────────────────────

    /// @notice Vehicle owner registers their wallet address for a vehicle ID
    function setVehicleOwner(string memory _vehicleId, address _owner) external onlyAdmin {
        require(bytes(_vehicleId).length > 0, "Empty vehicle ID");
        require(_owner != address(0), "Invalid owner address");
        _vehicleOwners[_vid(_vehicleId)] = _owner;
        emit VehicleOwnerSet(_vehicleId, _owner);
    }

    /// @notice Vehicle owner can self-register (must be called by the owner)
    function claimVehicle(string memory _vehicleId) external {
        require(bytes(_vehicleId).length > 0, "Empty vehicle ID");
        bytes32 vid = _vid(_vehicleId);
        require(_vehicleOwners[vid] == address(0), "Vehicle already claimed");
        _vehicleOwners[vid] = msg.sender;
        emit VehicleOwnerSet(_vehicleId, msg.sender);
    }

    // ───────────────── Public Accessors (backward compat) ────────────────

    /// @notice Get violation count for a vehicle (backward compatible)
    function violationCount(string memory _vehicleId) external view returns (uint256) {
        return _violationCount[_vid(_vehicleId)];
    }

    /// @notice Get fraud alert count for a vehicle (backward compatible)
    function fraudAlertCount(string memory _vehicleId) external view returns (uint256) {
        return _fraudAlertCount[_vid(_vehicleId)];
    }

    /// @notice Get consecutive pass count for a vehicle (backward compatible)
    function consecutivePassCount(string memory _vehicleId) external view returns (uint256) {
        return _consecutivePassCount[_vid(_vehicleId)];
    }

    /// @notice Get vehicle owner address (backward compatible)
    function vehicleOwners(string memory _vehicleId) external view returns (address) {
        return _vehicleOwners[_vid(_vehicleId)];
    }

    // ───────────────────────── Core: Store Emission ──────────────────────

    /**
     * @notice Store an emission record with device signature verification.
     * @dev Only authorized testing stations can call this. The device signature
     *      proves the data originated from a registered OBD device.
     *      CES is computed on-chain from raw pollutant values.
     *
     * @param _vehicleId     Vehicle registration number
     * @param _co2           CO2 emission (scaled x1000)
     * @param _co            CO emission (scaled x1000)
     * @param _nox           NOx emission (scaled x1000)
     * @param _hc            HC emission (scaled x1000)
     * @param _pm25          PM2.5 emission (scaled x1000)
     * @param _fraudScore    Fraud detection score (scaled x10000)
     * @param _vspValue      Vehicle Specific Power (scaled x1000)
     * @param _wltcPhase     WLTC phase: 0=Low, 1=Med, 2=High, 3=ExtraHigh
     * @param _timestamp     Unix epoch timestamp
     * @param _nonce         Unique nonce for replay protection
     * @param _deviceSignature ECDSA signature from the OBD device
     */
    function storeEmission(
        string memory _vehicleId,
        uint256 _co2,
        uint256 _co,
        uint256 _nox,
        uint256 _hc,
        uint256 _pm25,
        uint256 _fraudScore,
        uint256 _vspValue,
        uint8   _wltcPhase,
        uint256 _timestamp,
        bytes32 _nonce,
        bytes memory _deviceSignature
    ) external onlyStation nonReentrant {
        // Input validation
        require(bytes(_vehicleId).length > 0, "Vehicle ID cannot be empty");
        require(_timestamp > 0, "Timestamp must be greater than zero");
        require(_wltcPhase <= 3, "Invalid WLTC phase (0-3)");

        // Replay protection
        require(!usedNonces[_nonce], "Nonce already used");
        usedNonces[_nonce] = true;
        emit NonceUsed(_nonce);

        // Verify OBD device signature (now includes nonce)
        address deviceAddr = _verifyDeviceSignature(
            _vehicleId, _co2, _co, _nox, _hc, _pm25, _timestamp, _nonce, _deviceSignature
        );
        require(registeredDevices[deviceAddr], "Signature from unregistered device");

        // Compute CES on-chain
        uint256 cesScore = _computeCES(_co2, _co, _nox, _hc, _pm25);

        // Compliance check
        bool passed = (cesScore < CES_PASS_CEILING) && (_fraudScore < FRAUD_ALERT_THRESHOLD);

        bytes32 vid = _vid(_vehicleId);

        // Store record with full provenance
        emissionRecords[vid].push(EmissionRecord({
            vehicleId:      _vehicleId,
            co2:            _co2,
            co:             _co,
            nox:            _nox,
            hc:             _hc,
            pm25:           _pm25,
            cesScore:       cesScore,
            fraudScore:     _fraudScore,
            vspValue:       _vspValue,
            wltcPhase:      _wltcPhase,
            timestamp:      _timestamp,
            status:         passed,
            deviceAddress:  deviceAddr,
            stationAddress: msg.sender
        }));

        uint256 recordIndex = emissionRecords[vid].length - 1;

        // Update stats
        cesSumByVehicle[vid] += cesScore;

        // Auto-register vehicle. Soft cap is only enforced when set to a
        // non-zero value (controlled pilot mode); a zero value means no cap.
        if (!isRegistered[vid]) {
            require(
                softVehicleCap == 0 || vehicleCount < softVehicleCap,
                "Soft vehicle cap reached"
            );
            registeredVehicles.push(_vehicleId);
            isRegistered[vid] = true;
            vehicleCount++;
        }

        // Track consecutive passes for certificate eligibility
        if (passed) {
            _consecutivePassCount[vid]++;
            if (_consecutivePassCount[vid] == CONSECUTIVE_PASS_REQUIRED) {
                emit CertificateEligible(_vehicleId, _consecutivePassCount[vid]);
            }
        } else {
            _consecutivePassCount[vid] = 0;
            _violationCount[vid]++;
            violationIndices[vid].push(recordIndex);
            emit ViolationDetected(_vehicleId, cesScore, _timestamp);
        }

        // Track fraud alerts
        if (_fraudScore >= FRAUD_ALERT_THRESHOLD) {
            _fraudAlertCount[vid]++;
            emit FraudDetected(_vehicleId, _fraudScore, _timestamp);
        }

        // Emit per-pollutant violations
        if (_co2 > BSVI_CO2) emit PollutantViolation(_vehicleId, "CO2", _co2, BSVI_CO2);
        if (_co  > BSVI_CO)  emit PollutantViolation(_vehicleId, "CO",  _co,  BSVI_CO);
        if (_nox > BSVI_NOX) emit PollutantViolation(_vehicleId, "NOx", _nox, BSVI_NOX);
        if (_hc  > BSVI_HC)  emit PollutantViolation(_vehicleId, "HC",  _hc,  BSVI_HC);
        if (_pm25 > BSVI_PM25) emit PollutantViolation(_vehicleId, "PM25", _pm25, BSVI_PM25);

        emit RecordStored(_vehicleId, recordIndex, _timestamp, msg.sender, deviceAddr);
    }

    // ───────────────────────── Signature Verification ─────────────────────

    /**
     * @dev Recover the OBD device address from its ECDSA signature.
     *      The device signs: keccak256(vehicleId, co2, co, nox, hc, pm25, timestamp, nonce)
     */
    function _verifyDeviceSignature(
        string memory _vehicleId,
        uint256 _co2,
        uint256 _co,
        uint256 _nox,
        uint256 _hc,
        uint256 _pm25,
        uint256 _timestamp,
        bytes32 _nonce,
        bytes memory _signature
    ) internal pure returns (address) {
        bytes32 messageHash = keccak256(abi.encodePacked(
            _vehicleId, _co2, _co, _nox, _hc, _pm25, _timestamp, _nonce
        ));
        bytes32 ethSignedHash = messageHash.toEthSignedMessageHash();
        return ethSignedHash.recover(_signature);
    }

    /// @notice Public helper so off-chain code can compute the same hash (includes nonce)
    function getMessageHash(
        string memory _vehicleId,
        uint256 _co2,
        uint256 _co,
        uint256 _nox,
        uint256 _hc,
        uint256 _pm25,
        uint256 _timestamp,
        bytes32 _nonce
    ) external pure returns (bytes32) {
        return keccak256(abi.encodePacked(
            _vehicleId, _co2, _co, _nox, _hc, _pm25, _timestamp, _nonce
        ));
    }

    // ───────────────────────── CES Computation (Public) ──────────────────

    /// @notice Compute CES from raw pollutant values (public view for off-chain use)
    function computeCES(
        uint256 _co2,
        uint256 _co,
        uint256 _nox,
        uint256 _hc,
        uint256 _pm25
    ) external pure returns (uint256) {
        return _computeCES(_co2, _co, _nox, _hc, _pm25);
    }

    // ───────────────────────── View Functions ─────────────────────────────

    /// @notice Get aggregated stats for a vehicle
    function getVehicleStats(string memory _vehicleId)
        external view returns (
            uint256 totalRecords,
            uint256 violations,
            uint256 fraudAlerts,
            uint256 averageCES
        )
    {
        bytes32 vid = _vid(_vehicleId);
        totalRecords = emissionRecords[vid].length;
        violations = _violationCount[vid];
        fraudAlerts = _fraudAlertCount[vid];
        averageCES = totalRecords > 0 ? cesSumByVehicle[vid] / totalRecords : 0;
    }

    /// @notice Get a single record by index
    function getRecord(string memory _vehicleId, uint256 _index)
        external view returns (EmissionRecord memory)
    {
        bytes32 vid = _vid(_vehicleId);
        require(_index < emissionRecords[vid].length, "Index out of bounds");
        return emissionRecords[vid][_index];
    }

    /// @notice Get total record count for a vehicle
    function getRecordCount(string memory _vehicleId)
        external view returns (uint256)
    {
        return emissionRecords[_vid(_vehicleId)].length;
    }

    /// @notice Get paginated records for a vehicle (gas-safe)
    function getRecordsPaginated(
        string memory _vehicleId,
        uint256 _offset,
        uint256 _limit
    ) external view returns (EmissionRecord[] memory) {
        EmissionRecord[] storage records = emissionRecords[_vid(_vehicleId)];
        uint256 total = records.length;

        if (_offset >= total) return new EmissionRecord[](0);

        uint256 end = _offset + _limit;
        if (end > total) end = total;

        uint256 count = end - _offset;
        EmissionRecord[] memory page = new EmissionRecord[](count);
        for (uint256 i = 0; i < count; i++) {
            page[i] = records[_offset + i];
        }
        return page;
    }

    /// @notice Get all records for a vehicle (use paginated for large datasets)
    function getAllRecords(string memory _vehicleId)
        external view returns (EmissionRecord[] memory)
    {
        return emissionRecords[_vid(_vehicleId)];
    }

    /// @notice Get all FAIL records for a vehicle (backward compatible, O(n))
    function getViolations(string memory _vehicleId)
        external view returns (EmissionRecord[] memory)
    {
        bytes32 vid = _vid(_vehicleId);
        uint256[] storage indices = violationIndices[vid];
        EmissionRecord[] storage records = emissionRecords[vid];

        EmissionRecord[] memory violations = new EmissionRecord[](indices.length);
        for (uint256 i = 0; i < indices.length; i++) {
            violations[i] = records[indices[i]];
        }
        return violations;
    }

    /// @notice Get paginated FAIL records for a vehicle (gas-efficient)
    function getViolationsPaginated(
        string memory _vehicleId,
        uint256 _offset,
        uint256 _limit
    ) external view returns (EmissionRecord[] memory) {
        bytes32 vid = _vid(_vehicleId);
        uint256[] storage indices = violationIndices[vid];
        uint256 total = indices.length;

        if (_offset >= total) return new EmissionRecord[](0);

        uint256 end = _offset + _limit;
        if (end > total) end = total;

        uint256 count = end - _offset;
        EmissionRecord[] storage records = emissionRecords[vid];
        EmissionRecord[] memory page = new EmissionRecord[](count);
        for (uint256 i = 0; i < count; i++) {
            page[i] = records[indices[_offset + i]];
        }
        return page;
    }

    /// @notice Get the number of violation records for a vehicle
    function getViolationCount(string memory _vehicleId) external view returns (uint256) {
        return violationIndices[_vid(_vehicleId)].length;
    }

    /// @notice Get total registered vehicle count (for pagination)
    function getRegisteredVehicleCount() external view returns (uint256) {
        return registeredVehicles.length;
    }

    /// @notice Get paginated list of registered vehicle IDs
    function getRegisteredVehiclesPaginated(uint256 _offset, uint256 _limit)
        external view returns (string[] memory)
    {
        uint256 total = registeredVehicles.length;
        if (_offset >= total) return new string[](0);

        uint256 end = _offset + _limit;
        if (end > total) end = total;

        uint256 count = end - _offset;
        string[] memory page = new string[](count);
        for (uint256 i = 0; i < count; i++) {
            page[i] = registeredVehicles[_offset + i];
        }
        return page;
    }

    /// @notice Get all registered vehicle IDs
    function getRegisteredVehicles() external view returns (string[] memory) {
        return registeredVehicles;
    }

    /// @notice Check if a vehicle is eligible for PUC certificate
    function isCertificateEligible(string memory _vehicleId)
        external view returns (bool eligible, uint256 passes)
    {
        passes = _consecutivePassCount[_vid(_vehicleId)];
        eligible = passes >= CONSECUTIVE_PASS_REQUIRED;
    }
}
