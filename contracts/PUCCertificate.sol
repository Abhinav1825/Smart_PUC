// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "@openzeppelin/contracts-upgradeable/proxy/utils/Initializable.sol";
import "@openzeppelin/contracts-upgradeable/proxy/utils/UUPSUpgradeable.sol";
import "@openzeppelin/contracts-upgradeable/token/ERC721/ERC721Upgradeable.sol";
import "@openzeppelin/contracts-upgradeable/security/ReentrancyGuardUpgradeable.sol";
import "@openzeppelin/contracts-upgradeable/security/PausableUpgradeable.sol";

/**
 * @title IEmissionRegistry
 * @notice Interface for reading from the EmissionRegistry contract
 */
interface IEmissionRegistry {
    function getVehicleStats(string memory _vehicleId)
        external view returns (uint256 totalRecords, uint256 violations, uint256 fraudAlerts, uint256 averageCES);
    function consecutivePassCount(string memory _vehicleId) external view returns (uint256);
    function vehicleOwners(string memory _vehicleId) external view returns (address);
}

/**
 * @title IGreenToken
 * @notice Interface for minting Green Credit Tokens
 */
interface IGreenToken {
    function mint(address _to, uint256 _amount) external;
}

/**
 * @title PUCCertificate
 * @author Smart PUC Team
 * @notice ERC-721 NFT representing a digital Pollution Under Control (PUC) certificate.
 * @dev Reads from EmissionRegistry to verify eligibility before issuance.
 *      Auto-mints Green Credit Tokens (GCT) as rewards on issuance.
 *      Supports IPFS metadata via tokenURI override and base URI.
 *
 *      Certificate lifecycle:
 *        1. Vehicle passes >= 3 consecutive emission checks (tracked in EmissionRegistry)
 *        2. Testing station or vehicle owner calls issueCertificate()
 *        3. Contract verifies eligibility via EmissionRegistry
 *        4. NFT minted to vehicle owner, GreenTokens awarded
 *        5. Certificate valid for 180 days, can be revoked by authority
 *
 *      Scaling: averageCES is scaled x10000 (e.g., 0.85 => 8500).
 */
/**
 * @dev Storage layout rules (critical for UUPS upgrades)
 * -----------------------------------------------------
 * Do NOT reorder, insert, or delete state variables. New variables must
 * be appended AT THE END, and any deprecation MUST keep the slot by
 * leaving a placeholder. The `__gap` at the bottom reserves 50 slots.
 */
contract PUCCertificate is
    Initializable,
    UUPSUpgradeable,
    ERC721Upgradeable,
    ReentrancyGuardUpgradeable,
    PausableUpgradeable
{

    // ───────────────────────── State Variables ─────────────────────────

    /// @notice Authority address (deployer) — can issue/revoke certificates
    address public authority;

    /// @notice Reference to the EmissionRegistry contract
    IEmissionRegistry public emissionRegistry;

    /// @notice Reference to the GreenToken contract
    IGreenToken public greenToken;

    /// @notice Auto-incrementing token ID counter
    uint256 private _tokenIdCounter;

    /// @notice Default certificate validity duration (180 days).
    /// @dev Per CMVR Rule 115 / MoRTH G.S.R. 721(E) 2017, the renewal cycle
    ///      for a BS-VI four-wheeler is 180 days *after the first PUC*.
    uint256 public constant VALIDITY_PERIOD = 180 days;

    /// @notice Extended validity for a vehicle's first PUC after registration.
    /// @dev Per CMVR Rule 115 (BS-VI amendment), a newly-registered BS-VI
    ///      four-wheeler's first PUC is valid for **one year (360 days)**
    ///      before the 180-day renewal cycle takes over.
    uint256 public constant FIRST_PUC_VALIDITY_PERIOD = 360 days;

    /// @notice CES score ceiling for issuance (scaled x10000, must be < 10000)
    uint256 public constant CES_PASS_CEILING = 10000;

    /// @notice Minimum consecutive passes required (matches EmissionRegistry)
    uint256 public constant MIN_CONSECUTIVE_PASSES = 3;

    /// @notice Baseline Green Token reward constant (100 GCT), used as the
    ///         centre of the proportional reward formula below.
    uint256 public constant GREEN_TOKEN_REWARD = 100 * 10**18;

    /// @notice Minimum reward issued to any compliant vehicle (50 GCT).
    ///         A vehicle that only just clears the CES ceiling still gets
    ///         a token so that the incentive is not binary.
    uint256 public constant GREEN_TOKEN_REWARD_MIN = 50 * 10**18;

    /// @notice Maximum reward for a perfectly clean vehicle (200 GCT).
    ///         Encourages vehicles that drive significantly below BSVI.
    uint256 public constant GREEN_TOKEN_REWARD_MAX = 200 * 10**18;

    /// @notice Authorized testing stations that can trigger issuance
    mapping(address => bool) public authorizedIssuers;

    /// @notice Per-token metadata URIs (IPFS or other)
    mapping(uint256 => string) private _tokenURIs;

    /// @notice Base URI for IPFS gateway (e.g. "https://ipfs.io/ipfs/")
    string private _baseTokenURI;

    // ───────────────────────── Structs ─────────────────────────────────

    struct CertificateData {
        string  vehicleId;
        address vehicleOwner;
        uint256 issueTimestamp;
        uint256 expiryTimestamp;
        uint256 averageCES;          // scaled x10000
        uint256 totalRecordsAtIssue;
        address issuedByStation;
        bool    revoked;
        string  revokeReason;
        bool    isFirstPUC;          // true => 360-day validity; false => 180-day renewal
    }

    // ───────────────────────── Mappings ────────────────────────────────

    /// @notice Token ID => certificate data
    mapping(uint256 => CertificateData) public certificates;

    /// @notice Vehicle ID => latest certificate token ID
    mapping(string => uint256) public vehicleToCertificate;

    /// @notice Vehicle ID => has ever been issued a certificate
    mapping(string => bool) private hasCertificate;

    /// @notice Vehicle ID => total certificates issued (history count)
    mapping(string => uint256) public certificateCount;

    // ───────────────────────── Events ──────────────────────────────────

    event CertificateIssued(
        uint256 indexed tokenId,
        string  vehicleId,
        address indexed vehicleOwner,
        address indexed issuedBy,
        uint256 issueTimestamp,
        uint256 expiryTimestamp,
        uint256 averageCES
    );

    event CertificateRevoked(
        uint256 indexed tokenId,
        string  vehicleId,
        string  reason,
        address revokedBy
    );

    event CertificateExpired(
        uint256 indexed tokenId,
        string  vehicleId
    );

    event GreenTokensAwarded(
        string  indexed vehicleId,
        address indexed vehicleOwner,
        uint256 amount
    );

    event TokenURISet(
        uint256 indexed tokenId,
        string  uri
    );

    event BaseURISet(
        string  baseURI
    );

    // ───────────────────────── Modifiers ───────────────────────────────

    modifier onlyAuthority() {
        require(msg.sender == authority, "Only authority can call this function");
        _;
    }

    modifier onlyAuthorizedIssuer() {
        require(
            msg.sender == authority || authorizedIssuers[msg.sender],
            "Not authorized to issue certificates"
        );
        _;
    }

    // ───────────────────────── Storage Gap (UUPS) ─────────────────────
    // Reserved slots for future upgrades. Shrink only when adding new
    // state variables immediately before the gap.
    uint256[50] private __gap;

    // ───────────────────────── Initializer (UUPS) ─────────────────────

    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }

    /**
     * @param _emissionRegistry Address of the deployed EmissionRegistry contract
     * @param _greenToken       Address of the deployed GreenToken contract
     */
    function initialize(
        address _emissionRegistry,
        address _greenToken
    ) external initializer {
        __UUPSUpgradeable_init();
        __ERC721_init("PUC Certificate", "PUC");
        __ReentrancyGuard_init();
        __Pausable_init();

        authority = msg.sender;
        emissionRegistry = IEmissionRegistry(_emissionRegistry);
        greenToken = IGreenToken(_greenToken);
        _tokenIdCounter = 0;
    }

    // ───────────────────────── UUPS Upgrade Auth ─────────────────────

    /// @dev Only the authority role may authorize contract upgrades.
    function _authorizeUpgrade(address newImplementation) internal override onlyAuthority {
        // The onlyAuthority modifier is the access check.
    }

    // ───────────────────────── Admin Functions ─────────────────────────

    /// @notice Authorize a testing station to issue certificates
    function setAuthorizedIssuer(address _issuer, bool _authorized) external onlyAuthority {
        authorizedIssuers[_issuer] = _authorized;
    }

    /// @notice Update the EmissionRegistry address
    function setEmissionRegistry(address _addr) external onlyAuthority {
        emissionRegistry = IEmissionRegistry(_addr);
    }

    /// @notice Update the GreenToken address
    function setGreenToken(address _addr) external onlyAuthority {
        greenToken = IGreenToken(_addr);
    }

    /// @notice Transfer authority role
    function transferAuthority(address _newAuthority) external onlyAuthority {
        require(_newAuthority != address(0), "Invalid authority address");
        authority = _newAuthority;
    }

    /// @notice Pause certificate issuance (emergency circuit breaker).
    function pause() external onlyAuthority {
        _pause();
    }

    /// @notice Resume certificate issuance.
    function unpause() external onlyAuthority {
        _unpause();
    }

    /// @notice Set the base URI for IPFS gateway (e.g. "https://ipfs.io/ipfs/")
    function setBaseURI(string memory _newBaseURI) external onlyAuthority {
        _baseTokenURI = _newBaseURI;
        emit BaseURISet(_newBaseURI);
    }

    /**
     * @notice Set or update the metadata URI for a specific certificate token.
     * @param _tokenId The token ID to set the URI for
     * @param _uri     The metadata URI (IPFS CID or full URL)
     */
    function setTokenURI(uint256 _tokenId, string memory _uri) external onlyAuthority {
        require(_exists(_tokenId), "Token does not exist");
        _tokenURIs[_tokenId] = _uri;
        emit TokenURISet(_tokenId, _uri);
    }

    // ───────────────────────── Reward Calculation ─────────────────────

    /**
     * @notice Compute the concave Green Token reward for a given average
     *         CES score. Lower CES (cleaner vehicle) earns disproportionately
     *         more.
     * @dev The reward uses a quadratic concave curve that rewards genuinely
     *      clean vehicles more aggressively than the v3.1 linear curve:
     *
     *          delta    = CES_PASS_CEILING - ces            (0..CEILING)
     *          deltaSq  = delta^2 / CEILING                 (still 0..CEILING)
     *          reward   = MIN + (MAX - MIN) * deltaSq / CEILING
     *
     *      Key points along the curve (CEILING = 10000, MIN = 50 GCT,
     *      MAX = 200 GCT, spread = 150 GCT):
     *
     *        averageCES = 0         → 200 GCT   (perfect, full reward)
     *        averageCES = 2500      → ≈ 134 GCT (excellent)
     *        averageCES = 5000      → ≈ 87.5 GCT (mid-tier)
     *        averageCES = 7500      → ≈ 59 GCT (marginal)
     *        averageCES = 10000     → 50 GCT   (ceiling, minimum reward)
     *
     *      Compared to the earlier linear curve the concave formulation
     *      gives a stronger economic signal at the clean end of the
     *      spectrum while keeping the same boundary values, which is the
     *      behaviour a well-posed Pigouvian incentive should have.
     *      averageCES is scaled x10000, matching EmissionRegistry.
     */
    function computeRewardAmount(uint256 _averageCES) public pure returns (uint256) {
        // Cap the input so the formula stays well-defined.
        uint256 ces = _averageCES >= CES_PASS_CEILING ? CES_PASS_CEILING : _averageCES;

        uint256 spread = GREEN_TOKEN_REWARD_MAX - GREEN_TOKEN_REWARD_MIN;
        uint256 delta = CES_PASS_CEILING - ces;                    // 0..CEILING
        uint256 deltaSq = (delta * delta) / CES_PASS_CEILING;      // 0..CEILING

        // Concave interpolation: MIN + spread * deltaSq / CEILING
        return GREEN_TOKEN_REWARD_MIN + (spread * deltaSq) / CES_PASS_CEILING;
    }

    // ───────────────────────── Core Functions ──────────────────────────

    /**
     * @notice Issue a PUC certificate NFT after verifying eligibility from EmissionRegistry.
     * @param _vehicleId    Vehicle registration number
     * @param _vehicleOwner Wallet address of the vehicle owner (receives NFT + tokens)
     * @param _metadataURI  Optional IPFS metadata URI (pass "" if not ready yet)
     * @return tokenId The newly minted token ID
     *
     * @dev Eligibility checks:
     *   1. Vehicle has >= MIN_CONSECUTIVE_PASSES consecutive PASS records
     *   2. Average CES is below CES_PASS_CEILING
     *   3. No existing valid (non-expired, non-revoked) certificate
     */
    function issueCertificate(
        string memory _vehicleId,
        address _vehicleOwner,
        string memory _metadataURI
    ) external onlyAuthorizedIssuer nonReentrant whenNotPaused returns (uint256) {
        // Auto-detect "first PUC after registration": no prior certificate
        // on file for this vehicle ID. Callers who need explicit control
        // should use the 4-arg overload below.
        bool autoFirst = (certificateCount[_vehicleId] == 0);
        return _issueCertificateInternal(_vehicleId, _vehicleOwner, _metadataURI, autoFirst);
    }

    /**
     * @notice Backward-compatible overload without metadata URI parameter.
     * @param _vehicleId    Vehicle registration number
     * @param _vehicleOwner Wallet address of the vehicle owner (receives NFT + tokens)
     * @return tokenId The newly minted token ID
     */
    function issueCertificate(
        string memory _vehicleId,
        address _vehicleOwner
    ) external onlyAuthorizedIssuer nonReentrant whenNotPaused returns (uint256) {
        bool autoFirst = (certificateCount[_vehicleId] == 0);
        return _issueCertificateInternal(_vehicleId, _vehicleOwner, "", autoFirst);
    }

    /**
     * @notice Explicit-first-PUC overload.
     * @dev Lets the caller force the 360-day validity window for a vehicle's
     *      first PUC after registration, or explicitly mark a renewal as
     *      "not first" (e.g. a re-test after revocation). Closes audit L7.
     * @param _vehicleId    Vehicle registration number.
     * @param _vehicleOwner Wallet address of the vehicle owner.
     * @param _metadataURI  Optional IPFS metadata URI; pass "" to skip.
     * @param _isFirstPUC   ``true`` → 360-day validity (first post-registration
     *                      certificate); ``false`` → 180-day renewal cycle.
     * @return tokenId The newly minted token ID.
     */
    function issueCertificateWithFirstFlag(
        string memory _vehicleId,
        address _vehicleOwner,
        string memory _metadataURI,
        bool _isFirstPUC
    ) external onlyAuthorizedIssuer nonReentrant whenNotPaused returns (uint256) {
        return _issueCertificateInternal(_vehicleId, _vehicleOwner, _metadataURI, _isFirstPUC);
    }

    /**
     * @dev Internal implementation for certificate issuance.
     */
    function _issueCertificateInternal(
        string memory _vehicleId,
        address _vehicleOwner,
        string memory _metadataURI,
        bool _isFirstPUC
    ) internal returns (uint256) {
        require(bytes(_vehicleId).length > 0, "Vehicle ID cannot be empty");
        require(_vehicleOwner != address(0), "Invalid vehicle owner address");

        // Check if vehicle already has a valid certificate
        if (hasCertificate[_vehicleId]) {
            uint256 existingId = vehicleToCertificate[_vehicleId];
            CertificateData storage existing = certificates[existingId];
            require(
                existing.revoked || block.timestamp > existing.expiryTimestamp,
                "Vehicle already has a valid certificate"
            );
        }

        // Verify eligibility from EmissionRegistry
        uint256 consecutivePasses = emissionRegistry.consecutivePassCount(_vehicleId);
        require(consecutivePasses >= MIN_CONSECUTIVE_PASSES, "Insufficient consecutive passes");

        (uint256 totalRecords, , , uint256 averageCES) = emissionRegistry.getVehicleStats(_vehicleId);
        require(totalRecords > 0, "No emission records found");
        require(averageCES < CES_PASS_CEILING, "Average CES too high for certification");

        // Mint NFT
        _tokenIdCounter++;
        uint256 tokenId = _tokenIdCounter;
        uint256 issueTime = block.timestamp;
        // CMVR Rule 115 branch: a first post-registration PUC is valid for
        // one year, subsequent renewals for 180 days.
        uint256 validity = _isFirstPUC ? FIRST_PUC_VALIDITY_PERIOD : VALIDITY_PERIOD;
        uint256 expiryTime = issueTime + validity;

        _mint(_vehicleOwner, tokenId);

        // Set metadata URI if provided
        if (bytes(_metadataURI).length > 0) {
            _tokenURIs[tokenId] = _metadataURI;
            emit TokenURISet(tokenId, _metadataURI);
        }

        // Store certificate data
        certificates[tokenId] = CertificateData({
            vehicleId:          _vehicleId,
            vehicleOwner:       _vehicleOwner,
            issueTimestamp:     issueTime,
            expiryTimestamp:    expiryTime,
            averageCES:         averageCES,
            totalRecordsAtIssue: totalRecords,
            issuedByStation:    msg.sender,
            revoked:            false,
            revokeReason:       "",
            isFirstPUC:         _isFirstPUC
        });

        vehicleToCertificate[_vehicleId] = tokenId;
        hasCertificate[_vehicleId] = true;
        certificateCount[_vehicleId]++;

        emit CertificateIssued(
            tokenId, _vehicleId, _vehicleOwner, msg.sender,
            issueTime, expiryTime, averageCES
        );

        // Award Green Credit Tokens — amount is proportional to the
        // vehicle's averageCES so that cleaner vehicles receive larger
        // rewards. See computeRewardAmount() for the formula.
        uint256 rewardAmount = computeRewardAmount(averageCES);
        try greenToken.mint(_vehicleOwner, rewardAmount) {
            emit GreenTokensAwarded(_vehicleId, _vehicleOwner, rewardAmount);
        } catch {
            // GreenToken minting failure should not block certificate issuance
        }

        return tokenId;
    }

    /**
     * @notice Revoke a PUC certificate. Authority-only.
     * @param _tokenId Token ID of the certificate to revoke
     * @param _reason  Human-readable reason for revocation
     */
    function revokeCertificate(uint256 _tokenId, string memory _reason) external onlyAuthority {
        require(_exists(_tokenId), "Certificate does not exist");
        CertificateData storage cert = certificates[_tokenId];
        require(!cert.revoked, "Certificate already revoked");

        cert.revoked = true;
        cert.revokeReason = _reason;

        emit CertificateRevoked(_tokenId, cert.vehicleId, _reason, msg.sender);
    }

    // ───────────────────────── Token URI Functions ─────────────────────

    /**
     * @notice Returns the metadata URI for a given token.
     * @dev If a per-token URI is set, it is returned directly (or prepended with baseURI).
     *      If no per-token URI is set, returns empty string.
     * @param _tokenId The token ID to query
     * @return The full metadata URI string
     */
    function tokenURI(uint256 _tokenId) public view virtual override returns (string memory) {
        require(_exists(_tokenId), "ERC721Metadata: URI query for nonexistent token");

        string memory _uri = _tokenURIs[_tokenId];

        // If no per-token URI is set, return empty
        if (bytes(_uri).length == 0) {
            return "";
        }

        // If a base URI is set, concatenate base + per-token URI
        if (bytes(_baseTokenURI).length > 0) {
            return string(abi.encodePacked(_baseTokenURI, _uri));
        }

        // Otherwise return the per-token URI as-is (could be a full IPFS URL)
        return _uri;
    }

    // ───────────────────────── View Functions ──────────────────────────

    /**
     * @notice Check if a vehicle has a valid PUC certificate.
     * @param _vehicleId Vehicle registration number
     * @return valid True if certificate exists, is not revoked, and not expired
     * @return tokenId The certificate token ID (0 if none)
     * @return expiryTimestamp When the certificate expires (0 if none)
     */
    function isValid(string memory _vehicleId)
        external view returns (bool valid, uint256 tokenId, uint256 expiryTimestamp)
    {
        if (!hasCertificate[_vehicleId]) return (false, 0, 0);

        tokenId = vehicleToCertificate[_vehicleId];
        CertificateData storage cert = certificates[tokenId];

        if (cert.revoked) return (false, tokenId, cert.expiryTimestamp);
        if (block.timestamp > cert.expiryTimestamp) return (false, tokenId, cert.expiryTimestamp);

        return (true, tokenId, cert.expiryTimestamp);
    }

    /// @notice Get full certificate data for a token
    function getCertificate(uint256 _tokenId)
        external view returns (CertificateData memory)
    {
        require(_exists(_tokenId), "Certificate does not exist");
        return certificates[_tokenId];
    }

    /// @notice Get the latest certificate token ID for a vehicle
    function getVehicleCertificate(string memory _vehicleId)
        external view returns (uint256)
    {
        require(hasCertificate[_vehicleId], "No certificate for this vehicle");
        return vehicleToCertificate[_vehicleId];
    }

    /// @notice Get certificate data for QR code verification
    function getVerificationData(string memory _vehicleId)
        external view returns (
            bool valid,
            uint256 tokenId,
            string memory vehicleId,
            address vehicleOwner,
            uint256 issueDate,
            uint256 expiryDate,
            uint256 averageCES,
            bool revoked
        )
    {
        if (!hasCertificate[_vehicleId]) {
            return (false, 0, _vehicleId, address(0), 0, 0, 0, false);
        }

        tokenId = vehicleToCertificate[_vehicleId];
        CertificateData storage cert = certificates[tokenId];

        valid = !cert.revoked && block.timestamp <= cert.expiryTimestamp;
        vehicleId = cert.vehicleId;
        vehicleOwner = cert.vehicleOwner;
        issueDate = cert.issueTimestamp;
        expiryDate = cert.expiryTimestamp;
        averageCES = cert.averageCES;
        revoked = cert.revoked;
    }
}
