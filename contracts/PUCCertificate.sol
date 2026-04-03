// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "@openzeppelin/contracts/token/ERC721/ERC721.sol";

/**
 * @title PUCCertificate
 * @author Smart PUC Team
 * @notice ERC-721 NFT representing a digital Pollution Under Control (PUC) certificate.
 * @dev Each token corresponds to a PUC certificate issued to a vehicle that has passed
 *      emission compliance checks. Certificates expire after 180 days and can be revoked
 *      by the issuing authority.
 *
 *      Scaling: averageCES is scaled x10000 (e.g., 0.85 => 8500).
 */
contract PUCCertificate is ERC721 {

    // ───────────────────────── State Variables ─────────────────────────

    /// @notice Authority address (deployer) — only authority can issue/revoke certificates
    address public authority;

    /// @notice Auto-incrementing token ID counter
    uint256 private _tokenIdCounter;

    /// @notice Certificate validity duration in seconds (180 days)
    uint256 public constant VALIDITY_PERIOD = 180 days;

    /// @notice CES score ceiling for certificate issuance (scaled x10000). Must be < 10000
    uint256 public constant CES_PASS_CEILING = 10000;

    // ───────────────────────── Structs ─────────────────────────────────

    /**
     * @notice Data associated with each PUC certificate NFT.
     * @param vehicleId       Vehicle registration number
     * @param owner_          Address of the certificate holder
     * @param issueTimestamp   Unix epoch when the certificate was issued
     * @param expiryTimestamp  Unix epoch when the certificate expires (issue + 180 days)
     * @param averageCES      Average Composite Emission Score at issuance (scaled x10000)
     * @param emissionTxHash  Transaction hash of the emission record used for issuance
     */
    struct CertificateData {
        string  vehicleId;
        address owner_;
        uint256 issueTimestamp;
        uint256 expiryTimestamp;
        uint256 averageCES;
        bytes32 emissionTxHash;
    }

    // ───────────────────────── Mappings ────────────────────────────────

    /// @notice Token ID => certificate data
    mapping(uint256 => CertificateData) public certificates;

    /// @notice Vehicle ID => latest certificate token ID
    mapping(string => uint256) public vehicleToCertificate;

    /// @notice Token ID => revoked flag
    mapping(uint256 => bool) public revokedCertificates;

    /// @notice Tracks whether a vehicle has ever been issued a certificate
    mapping(string => bool) private hasCertificate;

    // ───────────────────────── Events ──────────────────────────────────

    /// @notice Emitted when a new PUC certificate is issued
    event CertificateIssued(
        uint256 indexed tokenId,
        string  vehicleId,
        address owner_,
        uint256 issueTimestamp,
        uint256 expiryTimestamp
    );

    /// @notice Emitted when a certificate is revoked
    event CertificateRevoked(
        uint256 indexed tokenId,
        string  reason
    );

    /// @notice Emitted when a validity check finds the certificate has expired
    event CertificateExpired(
        uint256 indexed tokenId,
        string  vehicleId
    );

    // ───────────────────────── Modifiers ───────────────────────────────

    /// @notice Restricts function access to the authority (deployer)
    modifier onlyAuthority() {
        require(msg.sender == authority, "Only authority can call this function");
        _;
    }

    // ───────────────────────── Constructor ─────────────────────────────

    /**
     * @notice Deploys the PUC Certificate NFT contract.
     * @dev Sets the deployer as the authority and initialises the ERC-721 with
     *      name "PUC Certificate" and symbol "PUC".
     */
    constructor() ERC721("PUC Certificate", "PUC") {
        authority = msg.sender;
        _tokenIdCounter = 0;
    }

    // ───────────────────────── Core Functions ──────────────────────────

    /**
     * @notice Issue a new PUC certificate NFT to the caller.
     * @param _vehicleId      Vehicle registration number
     * @param _averageCES     Average CES score (scaled x10000); must be < 10000 to qualify
     * @param _emissionTxHash Transaction hash of the underlying emission record
     * @return tokenId        The newly minted token ID
     *
     * @dev Only the authority can issue certificates. The certificate is minted to
     *      msg.sender (the authority) and is valid for 180 days from issuance.
     */
    function issueCertificate(
        string memory _vehicleId,
        uint256 _averageCES,
        bytes32 _emissionTxHash
    ) public onlyAuthority returns (uint256) {
        require(bytes(_vehicleId).length > 0, "Vehicle ID cannot be empty");
        require(_averageCES < CES_PASS_CEILING, "Average CES must be below 10000 to qualify");

        _tokenIdCounter++;
        uint256 tokenId = _tokenIdCounter;

        uint256 issueTime = block.timestamp;
        uint256 expiryTime = issueTime + VALIDITY_PERIOD;

        // Mint the NFT to the authority
        _mint(msg.sender, tokenId);

        // Store certificate data
        certificates[tokenId] = CertificateData({
            vehicleId:       _vehicleId,
            owner_:          msg.sender,
            issueTimestamp:   issueTime,
            expiryTimestamp:  expiryTime,
            averageCES:      _averageCES,
            emissionTxHash:  _emissionTxHash
        });

        // Update latest certificate mapping
        vehicleToCertificate[_vehicleId] = tokenId;
        hasCertificate[_vehicleId] = true;

        emit CertificateIssued(tokenId, _vehicleId, msg.sender, issueTime, expiryTime);

        return tokenId;
    }

    /**
     * @notice Check whether a vehicle has a valid (non-revoked, non-expired) PUC certificate.
     * @param _vehicleId Vehicle registration number
     * @return valid true if the vehicle's latest certificate exists, is not revoked, and has not expired
     *
     * @dev If the certificate is found to be expired, emits a CertificateExpired event
     *      (note: this is a view function, so the event is only emitted in non-static calls).
     */
    function isValid(
        string memory _vehicleId
    ) public view returns (bool valid) {
        // Check if the vehicle has ever been issued a certificate
        if (!hasCertificate[_vehicleId]) {
            return false;
        }

        uint256 tokenId = vehicleToCertificate[_vehicleId];

        // Check if revoked
        if (revokedCertificates[tokenId]) {
            return false;
        }

        // Check if expired
        CertificateData storage cert = certificates[tokenId];
        if (block.timestamp > cert.expiryTimestamp) {
            return false;
        }

        return true;
    }

    /**
     * @notice Revoke a PUC certificate. Authority-only.
     * @param _tokenId Token ID of the certificate to revoke
     * @param _reason  Human-readable reason for revocation
     *
     * @dev Marks the certificate as revoked. Does not burn the NFT so the
     *      historical record is preserved on-chain.
     */
    function revoke(
        uint256 _tokenId,
        string memory _reason
    ) public onlyAuthority {
        require(_ownerOf(_tokenId) != address(0), "Certificate does not exist");
        require(!revokedCertificates[_tokenId], "Certificate already revoked");

        revokedCertificates[_tokenId] = true;

        emit CertificateRevoked(_tokenId, _reason);
    }

    /**
     * @notice Get the full certificate data for a token.
     * @param _tokenId Token ID
     * @return The CertificateData struct
     */
    function getCertificate(
        uint256 _tokenId
    ) public view returns (CertificateData memory) {
        require(_ownerOf(_tokenId) != address(0), "Certificate does not exist");
        return certificates[_tokenId];
    }

    /**
     * @notice Get the latest certificate token ID for a vehicle.
     * @param _vehicleId Vehicle registration number
     * @return tokenId The latest token ID (0 if none exists)
     */
    function getVehicleCertificate(
        string memory _vehicleId
    ) public view returns (uint256) {
        require(hasCertificate[_vehicleId], "No certificate for this vehicle");
        return vehicleToCertificate[_vehicleId];
    }
}
