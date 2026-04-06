// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "@openzeppelin/contracts-upgradeable/proxy/utils/Initializable.sol";
import "@openzeppelin/contracts-upgradeable/proxy/utils/UUPSUpgradeable.sol";
import "@openzeppelin/contracts-upgradeable/security/ReentrancyGuardUpgradeable.sol";
import "@openzeppelin/contracts-upgradeable/security/PausableUpgradeable.sol";
import "@openzeppelin/contracts-upgradeable/utils/cryptography/EIP712Upgradeable.sol";
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
    ReentrancyGuardUpgradeable,
    PausableUpgradeable,
    EIP712Upgradeable
{
    using ECDSA for bytes32;

    // ─────────────────── BS-IV / BS-VI standards ─────────────────────────
    //
    // India's fleet is a mix of BS-III / BS-IV / BS-VI vehicles. This
    // contract natively supports the two most prevalent modern standards
    // (BS-IV and BS-VI) by keying CES normalisation off a per-vehicle
    // enum. Vehicles default to BS-VI; the admin sets the standard at
    // registration time via setVehicleStandard().
    enum BSStandard { BS6, BS4 }

    // ─────────────────── EIP-712 type hashes ─────────────────────────────
    // See _verifyDeviceSignature for the struct this binds.
    bytes32 private constant EMISSION_READING_TYPEHASH = keccak256(
        "EmissionReading(string vehicleId,uint256 co2,uint256 co,uint256 nox,uint256 hc,uint256 pm25,uint256 timestamp,bytes32 nonce)"
    );

    bytes32 private constant VEHICLE_CLAIM_TYPEHASH = keccak256(
        "VehicleClaim(string vehicleId,address claimant)"
    );

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

    // ───────────────── BS-IV Threshold Constants (scaled x1000) ──────────
    // BS-IV (Bharat Stage IV, 2010–2019) applies to roughly half of India's
    // in-use fleet and has looser caps than BS-VI. Vehicles registered
    // under BS-IV are normalised against these thresholds in _computeCES.
    //
    // Source: CMVR Rule 115 / MoRTH Notification S.O. 1114(E), 2001 and
    // ARAI BS-IV mass emission standards for M1 petrol passenger cars.

    uint256 public constant BS4_CO2  = 140000;  // 140 g/km x1000 (no BS-IV CO2 cap; using fleet-average target)
    uint256 public constant BS4_CO   = 2300;    // 2.3 g/km x1000
    uint256 public constant BS4_NOX  = 150;     // 0.150 g/km x1000
    uint256 public constant BS4_HC   = 200;     // 0.200 g/km x1000 (combined HC+NOx 0.35 – BS4_NOX)
    uint256 public constant BS4_PM25 = 25;      // 0.025 g/km x1000

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

    // ──────────────────── v3.2 append-only state ─────────────────────────
    // New storage slots added in v3.2 (EIP-712, BS-IV support, Merkle batch
    // commitments, phase summaries). Appended AFTER all pre-existing state
    // and BEFORE the storage gap, so upgrades from v3.1 proxies are safe.

    /// @notice Per-vehicle BS standard. Default (slot 0) = BS6. The admin
    ///         calls setVehicleStandard to tag BS-IV vehicles; the next
    ///         storeEmission call will use the BS-IV threshold table.
    mapping(bytes32 => BSStandard) private _vehicleStandard;

    /// @notice Per-vehicle daily Merkle commitment: keccak256-committed
    ///         root of a batch of off-chain telemetry readings. Used by the
    ///         hot/cold storage separation (see backend/merkle_batch.py).
    ///         Key is keccak256(abi.encodePacked(vehicleId, dayIndex)).
    mapping(bytes32 => bytes32) public batchRoots;

    /// @notice Number of readings committed under each batch root, for
    ///         off-chain verification that the Merkle tree has the
    ///         expected leaf count.
    mapping(bytes32 => uint256) public batchRootCount;

    /// @notice Per-vehicle last-write timestamp. Used by the contract-level
    ///         rate limit below to stop a compromised testing station
    ///         from flooding a single vehicle with synthetic readings
    ///         (audit report G8 / S7).
    mapping(bytes32 => uint256) private _lastWriteTimestamp;

    /// @notice Minimum gap in seconds between two storeEmission writes
    ///         for the same vehicle. Default 3s matches the fastest
    ///         realistic OBD-II polling rate; the admin can tune this
    ///         per-deployment via setPerVehicleRateLimit.
    uint256 public perVehicleRateLimitSeconds;

    /// @notice Optional privacy mode (audit L11 / G6). When enabled, every
    ///         storeEmission additionally emits an EmissionStoredHashed event
    ///         whose indexed field is the keccak256 hash of the plaintext
    ///         vehicleId, allowing off-chain consumers to index a vehicle's
    ///         stream by salted hash only (if the caller supplies a salted
    ///         id string) while never exposing the plaintext through logs.
    ///         Defaults to FALSE so existing integrators — including
    ///         scripts/e2e_business_flow.py and the frontend's event
    ///         listener — continue to work without any change.
    bool public privacyMode;

    // ─── v4.1: Tiered Compliance (PUC interval extension) ───────────
    enum ComplianceTier { Unclassified, Bronze, Silver, Gold }

    mapping(bytes32 => ComplianceTier) private _vehicleTier;
    mapping(bytes32 => uint256) private _tierLastUpdated;
    // Tier thresholds (CES scaled x10000)
    uint256 public constant TIER_GOLD_CES_MAX = 5000;      // CES < 0.5
    uint256 public constant TIER_SILVER_CES_MAX = 7500;     // CES < 0.75
    uint256 public constant TIER_GOLD_MIN_RECORDS = 50;
    uint256 public constant TIER_SILVER_MIN_RECORDS = 20;
    uint256 public constant TIER_BRONZE_MIN_RECORDS = 5;

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

    /// @notice Privacy-preserving twin of RecordStored (audit L11 / G6).
    ///         Only emitted when privacyMode == true. Carries the hashed
    ///         vehicle id in an indexed topic so off-chain indexers can
    ///         filter by hash without ever seeing the plaintext registration
    ///         number in event logs.
    event EmissionStoredHashed(
        bytes32 indexed vehicleIdHash,
        uint256 recordIndex,
        uint256 cesScore,
        uint256 fraudScore,
        bool    passed,
        uint256 timestamp
    );

    /// @notice Emitted when the admin toggles privacy mode on/off.
    event PrivacyModeSet(bool enabled);

    /// @notice Emitted when a vehicle's compliance tier changes.
    event VehicleTierUpdated(string vehicleId, uint8 oldTier, uint8 newTier, uint256 timestamp);

    event StationUpdated(address indexed station, bool authorized);
    event DeviceUpdated(address indexed device, bool registered);
    event VehicleOwnerSet(string indexed vehicleId, address indexed owner);
    event NonceUsed(bytes32 indexed nonce);

    /// @notice Emitted when the admin tags a vehicle with its BS standard.
    event VehicleStandardSet(string indexed vehicleId, BSStandard standard);

    /// @notice Emitted when a testing station reports an aggregated
    ///         per-phase summary for a WLTC cycle. Enables phase-weighted
    ///         compliance analysis off-chain.
    event PhaseCompleted(
        string  indexed vehicleId,
        uint8   phase,
        uint256 avgCES,
        uint256 distanceMeters,
        uint256 timestamp
    );

    /// @notice Emitted when a testing station commits a Merkle root of a
    ///         batch of off-chain telemetry readings.
    event BatchRootCommitted(
        string  indexed vehicleId,
        uint256 dayIndex,
        bytes32 root,
        uint256 count
    );

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
    //
    // Layout history:
    //   v3.2 initial gap = 50
    //   v3.2.1 added _lastWriteTimestamp mapping (+1 slot)
    //   v3.2.1 added perVehicleRateLimitSeconds (+1 slot)
    //   v3.2.2 added privacyMode bool (+1 slot; packed into its own slot)
    //   v4.1   added _vehicleTier mapping (+1 slot), _tierLastUpdated mapping (+1 slot)
    //   Current remaining = 45
    uint256[45] private __gap;

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
        __Pausable_init();
        __EIP712_init("SmartPUC", "3.2");
        admin = msg.sender;
        // Admin is also an authorized station for initial setup/testing.
        testingStations[msg.sender] = true;
        // Per-vehicle rate limit defaults to DISABLED (0) so that the
        // upgrade is safe against existing integration tests and replay
        // of historical batch imports. Production deployments MUST call
        // setPerVehicleRateLimit(3) (or higher) immediately after deploy
        // to close audit-report G8 / S7 ("compromised station flood
        // attack on a single vehicle"). See docs/THREAT_MODEL.md §A7.
        perVehicleRateLimitSeconds = 0;
    }

    /// @notice Admin tunable for the per-vehicle write rate limit.
    /// @dev Set to 0 to disable the check (useful for integration tests
    ///      that want to stuff many historical readings in one block).
    function setPerVehicleRateLimit(uint256 _seconds) external onlyAdmin {
        perVehicleRateLimitSeconds = _seconds;
    }

    /// @notice Toggle privacy mode (audit L11 / G6). When enabled,
    ///         storeEmission will additionally emit EmissionStoredHashed
    ///         with only the hashed vehicle id in the indexed topic,
    ///         giving off-chain indexers a privacy-preserving channel.
    ///         Defaults to FALSE on initialize() so no existing integrator
    ///         is affected.
    function setPrivacyMode(bool _enabled) external onlyAdmin {
        privacyMode = _enabled;
        emit PrivacyModeSet(_enabled);
    }

    /// @notice Public pure helper to compute the keccak256 hash of a
    ///         (possibly salted) vehicle id string. Off-chain callers can
    ///         use this as the authoritative hashing function so their
    ///         privacy-preserving indices match the on-chain event topic.
    function computeVehicleIdHash(string memory _vehicleId) external pure returns (bytes32) {
        return keccak256(bytes(_vehicleId));
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

    /// @dev Compute CES on-chain from raw pollutant values (all scaled x1000)
    ///      against the appropriate BS-IV or BS-VI threshold set. Returns
    ///      CES scaled x10000; at the standard's threshold the sum equals
    ///      CES_WEIGHT_TOTAL = 10000 = CES_PASS_CEILING.
    ///
    ///      Disclosure: the weight distribution (0.35/0.30/0.15/0.12/0.08)
    ///      is a **proposed composite scoring scheme**, not an ARAI or
    ///      MoRTH standard. See docs/ARCHITECTURE_TRADEOFFS.md for the
    ///      design rationale.
    function _computeCES(
        uint256 _co2,
        uint256 _co,
        uint256 _nox,
        uint256 _hc,
        uint256 _pm25,
        BSStandard _std
    ) internal pure returns (uint256) {
        if (_std == BSStandard.BS4) {
            return (_co2 * CES_WEIGHT_CO2 / BS4_CO2)
                 + (_nox * CES_WEIGHT_NOX / BS4_NOX)
                 + (_co  * CES_WEIGHT_CO  / BS4_CO)
                 + (_hc  * CES_WEIGHT_HC  / BS4_HC)
                 + (_pm25 * CES_WEIGHT_PM25 / BS4_PM25);
        }
        return (_co2 * CES_WEIGHT_CO2 / BSVI_CO2)
             + (_nox * CES_WEIGHT_NOX / BSVI_NOX)
             + (_co  * CES_WEIGHT_CO  / BSVI_CO)
             + (_hc  * CES_WEIGHT_HC  / BSVI_HC)
             + (_pm25 * CES_WEIGHT_PM25 / BSVI_PM25);
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

    /// @notice Pause all state-mutating entry points (storeEmission,
    ///         reportPhaseSummary, commitBatchRoot, claimVehicle). Used
    ///         as an emergency circuit breaker.
    function pause() external onlyAdmin {
        _pause();
    }

    /// @notice Resume normal operation after a pause.
    function unpause() external onlyAdmin {
        _unpause();
    }

    /// @notice Tag a vehicle with its Bharat-Stage standard (BS-IV / BS-VI).
    ///         Default for unset vehicles is BS6. Must be called before
    ///         the vehicle's first storeEmission to take effect on that
    ///         record.
    function setVehicleStandard(string memory _vehicleId, BSStandard _std) external onlyAdmin {
        require(bytes(_vehicleId).length > 0, "Empty vehicle ID");
        _vehicleStandard[_vid(_vehicleId)] = _std;
        emit VehicleStandardSet(_vehicleId, _std);
    }

    /// @notice Read a vehicle's applicable BS standard.
    function vehicleStandard(string memory _vehicleId) external view returns (BSStandard) {
        return _vehicleStandard[_vid(_vehicleId)];
    }

    // ───────────────────────── Vehicle Owner ──────────────────────────────

    /// @notice Vehicle owner registers their wallet address for a vehicle ID
    function setVehicleOwner(string memory _vehicleId, address _owner) external onlyAdmin {
        require(bytes(_vehicleId).length > 0, "Empty vehicle ID");
        require(_owner != address(0), "Invalid owner address");
        _vehicleOwners[_vid(_vehicleId)] = _owner;
        emit VehicleOwnerSet(_vehicleId, _owner);
    }

    /// @notice Vehicle owner self-registration with admin-signed proof.
    /// @dev    v3.1 had a permissionless `claimVehicle(string)` that was
    ///         exploitable by squatters: any caller could claim any
    ///         unclaimed vehicle ID. v3.2 requires an EIP-712 signature
    ///         from the admin key over the tuple (vehicleId, claimant)
    ///         as proof of off-chain authorisation (typically issued by
    ///         the RTO after KYC).
    /// @param _vehicleId   Vehicle registration number to claim.
    /// @param _adminSig    EIP-712 signature of VehicleClaim(vehicleId, msg.sender)
    ///                     by the current admin.
    function claimVehicle(string memory _vehicleId, bytes memory _adminSig)
        external
        whenNotPaused
    {
        require(bytes(_vehicleId).length > 0, "Empty vehicle ID");
        bytes32 vid = _vid(_vehicleId);
        require(_vehicleOwners[vid] == address(0), "Vehicle already claimed");

        bytes32 structHash = keccak256(abi.encode(
            VEHICLE_CLAIM_TYPEHASH,
            keccak256(bytes(_vehicleId)),
            msg.sender
        ));
        bytes32 digest = _hashTypedDataV4(structHash);
        address signer = ECDSA.recover(digest, _adminSig);
        require(signer == admin, "Invalid admin signature");

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
    ) external onlyStation nonReentrant whenNotPaused {
        // Input validation
        require(bytes(_vehicleId).length > 0, "Vehicle ID cannot be empty");
        require(_timestamp > 0, "Timestamp must be greater than zero");
        require(_wltcPhase <= 3, "Invalid WLTC phase (0-3)");

        // Replay protection
        require(!usedNonces[_nonce], "Nonce already used");
        usedNonces[_nonce] = true;
        emit NonceUsed(_nonce);

        bytes32 vid = _vid(_vehicleId);

        // Per-vehicle contract-level rate limit (audit G8 / S7).
        // Guards against a compromised testing station flooding a
        // single vehicle with back-to-back synthetic readings.
        if (perVehicleRateLimitSeconds > 0) {
            uint256 lastWrite = _lastWriteTimestamp[vid];
            if (lastWrite != 0) {
                require(
                    block.timestamp >= lastWrite + perVehicleRateLimitSeconds,
                    "Per-vehicle rate limit: writes too frequent"
                );
            }
            _lastWriteTimestamp[vid] = block.timestamp;
        }

        // Verify OBD device EIP-712 signature (chain-id bound via domain)
        address deviceAddr = _verifyDeviceSignature(
            _vehicleId, _co2, _co, _nox, _hc, _pm25, _timestamp, _nonce, _deviceSignature
        );
        require(registeredDevices[deviceAddr], "Signature from unregistered device");

        // Compute CES using the vehicle's applicable BS standard
        BSStandard std = _vehicleStandard[vid];
        uint256 cesScore = _computeCES(_co2, _co, _nox, _hc, _pm25, std);

        // Compliance check
        bool passed = (cesScore < CES_PASS_CEILING) && (_fraudScore < FRAUD_ALERT_THRESHOLD);

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

        // Emit per-pollutant violations against the appropriate standard
        {
            uint256 tCo2  = std == BSStandard.BS4 ? BS4_CO2  : BSVI_CO2;
            uint256 tCo   = std == BSStandard.BS4 ? BS4_CO   : BSVI_CO;
            uint256 tNox  = std == BSStandard.BS4 ? BS4_NOX  : BSVI_NOX;
            uint256 tHc   = std == BSStandard.BS4 ? BS4_HC   : BSVI_HC;
            uint256 tPm25 = std == BSStandard.BS4 ? BS4_PM25 : BSVI_PM25;
            if (_co2 > tCo2)  emit PollutantViolation(_vehicleId, "CO2",  _co2,  tCo2);
            if (_co  > tCo)   emit PollutantViolation(_vehicleId, "CO",   _co,   tCo);
            if (_nox > tNox)  emit PollutantViolation(_vehicleId, "NOx",  _nox,  tNox);
            if (_hc  > tHc)   emit PollutantViolation(_vehicleId, "HC",   _hc,   tHc);
            if (_pm25 > tPm25) emit PollutantViolation(_vehicleId, "PM25", _pm25, tPm25);
        }

        emit RecordStored(_vehicleId, recordIndex, _timestamp, msg.sender, deviceAddr);

        // Privacy mode twin-event (audit L11 / G6). Only emitted when the
        // admin has opted in via setPrivacyMode(true). Carries the hashed
        // vehicle id so off-chain indexers can filter by hash without ever
        // seeing the plaintext registration number in event topics.
        if (privacyMode) {
            emit EmissionStoredHashed(
                vid,
                recordIndex,
                cesScore,
                _fraudScore,
                passed,
                _timestamp
            );
        }

        // v4.1: Update tiered compliance after every emission record
        _updateVehicleTier(vid, _vehicleId);
    }

    // ───────────────────────── Per-phase summary ─────────────────────────

    /// @notice Commit an aggregated WLTC-phase summary for a vehicle.
    ///         Complements per-reading submissions by emitting a single
    ///         event per phase that off-chain auditors can use to compute
    ///         phase-weighted compliance without replaying the full stream.
    /// @param _vehicleId      Vehicle registration number.
    /// @param _phase          WLTC phase (0=Low, 1=Medium, 2=High, 3=Extra High).
    /// @param _avgCES         Average CES over the phase (scaled x10000).
    /// @param _distanceMeters Distance covered in this phase, in metres.
    /// @param _timestamp      Unix epoch timestamp at phase completion.
    function reportPhaseSummary(
        string memory _vehicleId,
        uint8   _phase,
        uint256 _avgCES,
        uint256 _distanceMeters,
        uint256 _timestamp
    ) external onlyStation whenNotPaused {
        require(bytes(_vehicleId).length > 0, "Empty vehicle ID");
        require(_phase <= 3, "Invalid WLTC phase (0-3)");
        require(_timestamp > 0, "Timestamp must be greater than zero");
        emit PhaseCompleted(_vehicleId, _phase, _avgCES, _distanceMeters, _timestamp);
    }

    // ───────────────────────── Merkle batch commits ──────────────────────

    /// @notice Commit a Merkle root of a batch of off-chain telemetry
    ///         readings. Enables the hot/cold storage separation described
    ///         in docs/ARCHITECTURE_TRADEOFFS.md §6: fine-grained readings
    ///         live in a station's off-chain SQLite database, only the
    ///         committed root is anchored on-chain, and a verifier can
    ///         prove any individual reading was in the committed batch via
    ///         a Merkle proof against this root.
    /// @param _vehicleId Vehicle registration number.
    /// @param _dayIndex  Index of the day being committed (e.g. days since epoch).
    /// @param _root      keccak256 root of the Merkle tree over the day's readings.
    /// @param _count     Number of leaves in the committed tree.
    function commitBatchRoot(
        string memory _vehicleId,
        uint256 _dayIndex,
        bytes32 _root,
        uint256 _count
    ) external onlyStation whenNotPaused {
        require(bytes(_vehicleId).length > 0, "Empty vehicle ID");
        require(_root != bytes32(0), "Empty Merkle root");
        require(_count > 0, "Empty batch");
        bytes32 key = keccak256(abi.encodePacked(_vehicleId, _dayIndex));
        require(batchRoots[key] == bytes32(0), "Batch already committed");
        batchRoots[key] = _root;
        batchRootCount[key] = _count;
        emit BatchRootCommitted(_vehicleId, _dayIndex, _root, _count);
    }

    /// @notice Look up a previously committed Merkle root for a vehicle/day.
    function getBatchRoot(string memory _vehicleId, uint256 _dayIndex)
        external view returns (bytes32 root, uint256 count)
    {
        bytes32 key = keccak256(abi.encodePacked(_vehicleId, _dayIndex));
        return (batchRoots[key], batchRootCount[key]);
    }

    // ───────────────────────── Signature Verification ─────────────────────

    /**
     * @dev Recover the OBD device address from its EIP-712 signature.
     *      The device signs the structured type:
     *          EmissionReading(string vehicleId,uint256 co2,uint256 co,
     *                          uint256 nox,uint256 hc,uint256 pm25,
     *                          uint256 timestamp,bytes32 nonce)
     *      under the domain ("SmartPUC", "3.2", chainId, verifyingContract).
     *      Chain-id binding in the domain separator makes cross-chain
     *      replay (threat A9) infeasible — a signature is valid only on
     *      the chain the domain identifies.
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
    ) internal view returns (address) {
        bytes32 structHash = keccak256(abi.encode(
            EMISSION_READING_TYPEHASH,
            keccak256(bytes(_vehicleId)),
            _co2, _co, _nox, _hc, _pm25,
            _timestamp, _nonce
        ));
        bytes32 digest = _hashTypedDataV4(structHash);
        return ECDSA.recover(digest, _signature);
    }

    /// @notice Public helper so off-chain code can compute the same EIP-712
    ///         digest the contract uses to verify device signatures. This
    ///         replaces the legacy abi.encodePacked keccak256 that v3.1
    ///         used — the new digest binds chain-id and contract address
    ///         via the domain separator.
    function getEmissionDigest(
        string memory _vehicleId,
        uint256 _co2,
        uint256 _co,
        uint256 _nox,
        uint256 _hc,
        uint256 _pm25,
        uint256 _timestamp,
        bytes32 _nonce
    ) external view returns (bytes32) {
        bytes32 structHash = keccak256(abi.encode(
            EMISSION_READING_TYPEHASH,
            keccak256(bytes(_vehicleId)),
            _co2, _co, _nox, _hc, _pm25,
            _timestamp, _nonce
        ));
        return _hashTypedDataV4(structHash);
    }

    /// @notice Public helper so off-chain code can compute the EIP-712
    ///         digest for a VehicleClaim payload (used by claimVehicle).
    function getVehicleClaimDigest(string memory _vehicleId, address _claimant)
        external view returns (bytes32)
    {
        bytes32 structHash = keccak256(abi.encode(
            VEHICLE_CLAIM_TYPEHASH,
            keccak256(bytes(_vehicleId)),
            _claimant
        ));
        return _hashTypedDataV4(structHash);
    }

    // ───────────────────────── CES Computation (Public) ──────────────────

    /// @notice Compute CES against BS-VI thresholds (public view for off-chain use).
    function computeCES(
        uint256 _co2,
        uint256 _co,
        uint256 _nox,
        uint256 _hc,
        uint256 _pm25
    ) external pure returns (uint256) {
        return _computeCES(_co2, _co, _nox, _hc, _pm25, BSStandard.BS6);
    }

    /// @notice Compute CES against an explicit BS standard.
    function computeCESForStandard(
        uint256 _co2,
        uint256 _co,
        uint256 _nox,
        uint256 _hc,
        uint256 _pm25,
        BSStandard _std
    ) external pure returns (uint256) {
        return _computeCES(_co2, _co, _nox, _hc, _pm25, _std);
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

    // ───────────────────────── v4.1 Tiered Compliance ───────────────────

    /**
     * @notice Get the compliance tier of a vehicle.
     * @param _vehicleId The vehicle registration number
     * @return tier The ComplianceTier enum value (0=Unclassified, 1=Bronze, 2=Silver, 3=Gold)
     */
    function getVehicleTier(string memory _vehicleId) external view returns (uint8) {
        bytes32 vid = keccak256(bytes(_vehicleId));
        return uint8(_vehicleTier[vid]);
    }

    /**
     * @notice Admin override to manually set a vehicle's tier.
     * @param _vehicleId The vehicle registration number
     * @param _tier The tier to assign (0-3)
     */
    function setVehicleTierManually(string memory _vehicleId, uint8 _tier)
        external onlyAdmin whenNotPaused
    {
        require(_tier <= uint8(ComplianceTier.Gold), "Invalid tier");
        bytes32 vid = keccak256(bytes(_vehicleId));
        ComplianceTier oldTier = _vehicleTier[vid];
        _vehicleTier[vid] = ComplianceTier(_tier);
        _tierLastUpdated[vid] = block.timestamp;
        emit VehicleTierUpdated(_vehicleId, uint8(oldTier), _tier, block.timestamp);
    }

    /**
     * @dev Internal: recompute and update a vehicle's compliance tier.
     *      Called at the end of every storeEmission(). O(1) — no loops.
     */
    function _updateVehicleTier(bytes32 _vid, string memory _vehicleId) internal {
        // Get vehicle stats
        uint256 totalRecs = emissionRecords[_vid].length;
        uint256 avgCES = (totalRecs > 0) ? cesSumByVehicle[_vid] / totalRecs : CES_PASS_CEILING;
        uint256 fraudAlerts = _fraudAlertCount[_vid];

        ComplianceTier oldTier = _vehicleTier[_vid];
        ComplianceTier newTier = ComplianceTier.Unclassified;

        // Gold: avgCES < 5000, 50+ records, 0 fraud
        if (avgCES < TIER_GOLD_CES_MAX && totalRecs >= TIER_GOLD_MIN_RECORDS && fraudAlerts == 0) {
            newTier = ComplianceTier.Gold;
        }
        // Silver: avgCES < 7500, 20+ records, <=1 fraud
        else if (avgCES < TIER_SILVER_CES_MAX && totalRecs >= TIER_SILVER_MIN_RECORDS && fraudAlerts <= 1) {
            newTier = ComplianceTier.Silver;
        }
        // Bronze: avgCES < 10000, 5+ records
        else if (avgCES < CES_PASS_CEILING && totalRecs >= TIER_BRONZE_MIN_RECORDS) {
            newTier = ComplianceTier.Bronze;
        }

        if (newTier != oldTier) {
            _vehicleTier[_vid] = newTier;
            _tierLastUpdated[_vid] = block.timestamp;
            emit VehicleTierUpdated(_vehicleId, uint8(oldTier), uint8(newTier), block.timestamp);
        }
    }
}
