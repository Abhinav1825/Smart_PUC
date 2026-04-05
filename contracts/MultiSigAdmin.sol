// SPDX-License-Identifier: MIT
pragma solidity ^0.8.21;

/**
 * @title  MultiSigAdmin
 * @notice Minimal N-of-M multisig that holds the `admin` role for the
 *         Smart PUC EmissionRegistry / PUCCertificate / GreenToken
 *         contracts. Closes audit item S5 ("single-EOA admin is a
 *         single point of failure — a compromised admin key can pause
 *         the registry or re-assign every vehicle owner").
 *
 * Design principles
 * -----------------
 * - **Minimal surface area.** We deliberately avoid Gnosis Safe's
 *   enormous module system because (a) it costs ~1.6 million gas to
 *   deploy and (b) it would pull in 40+ external Solidity files that
 *   reviewers would have to trust. This contract fits in ~180 lines
 *   and can be audited in a single sitting.
 * - **No upgradability.** The multisig is a static contract. If the
 *   signer set needs to change, deploy a new multisig and call
 *   `transferAdmin(newMultisig)` on each owned contract. This is
 *   intentional: an upgradeable multisig would itself need an admin
 *   and we would be right back where we started.
 * - **Transaction queue.** Any signer can propose a transaction to
 *   any target (typically the EmissionRegistry). Once `threshold`
 *   signers have confirmed, any signer can execute it.
 * - **Replay-safe.** Each proposal has a unique id and an `executed`
 *   flag; re-execution reverts.
 *
 * Usage
 * -----
 * 1. Deploy `MultiSigAdmin` with an array of signer addresses and a
 *    confirmation threshold. E.g. `new MultiSigAdmin([sig1, sig2, sig3], 2)`
 *    creates a 2-of-3 multisig.
 * 2. On each already-deployed contract (EmissionRegistry, etc.), the
 *    current admin calls `transferAdmin(multisigAddress)`. From that
 *    point on, every onlyAdmin call must go through the multisig.
 * 3. A signer calls `propose(target, data)` to queue an admin call
 *    (e.g. `registry.setPerVehicleRateLimit(3)`).
 * 4. The other signers call `confirm(proposalId)`. When the count
 *    reaches `threshold`, any signer calls `execute(proposalId)`.
 *
 * Audit scope (for the paper)
 * ---------------------------
 * This contract is a *governance primitive*, not a research
 * contribution. Its purpose is to raise the attacker cost for
 * administrative actions from one compromised key to `threshold`
 * compromised keys. It is NOT a substitute for DAO governance or a
 * timelock; see `docs/MULTISIG.md` for the full threat model.
 */
contract MultiSigAdmin {
    // ─────────────────────── Storage ───────────────────────

    address[] public signers;
    mapping(address => bool) public isSigner;
    uint256 public threshold;

    struct Proposal {
        address target;
        bytes   data;
        uint256 value;
        bool    executed;
        uint256 confirmations;
        mapping(address => bool) confirmedBy;
    }

    uint256 public proposalCount;
    mapping(uint256 => Proposal) private _proposals;

    // ─────────────────────── Events ────────────────────────

    event Proposed(uint256 indexed id, address indexed proposer, address indexed target, bytes data, uint256 value);
    event Confirmed(uint256 indexed id, address indexed signer, uint256 confirmations);
    event Executed(uint256 indexed id, address indexed executor, bool success);
    event Revoked(uint256 indexed id, address indexed signer);

    // ─────────────────────── Errors ────────────────────────

    error NotSigner();
    error ProposalMissing();
    error AlreadyExecuted();
    error AlreadyConfirmed();
    error NotConfirmed();
    error BelowThreshold();
    error ExecutionFailed();

    // ─────────────────────── Modifiers ─────────────────────

    modifier onlySigner() {
        if (!isSigner[msg.sender]) revert NotSigner();
        _;
    }

    modifier proposalExists(uint256 _id) {
        if (_id >= proposalCount) revert ProposalMissing();
        _;
    }

    // ─────────────────────── Constructor ───────────────────

    constructor(address[] memory _signers, uint256 _threshold) {
        require(_signers.length >= 1, "MultiSigAdmin: need >=1 signer");
        require(_threshold >= 1 && _threshold <= _signers.length, "MultiSigAdmin: invalid threshold");
        for (uint256 i = 0; i < _signers.length; i++) {
            address s = _signers[i];
            require(s != address(0), "MultiSigAdmin: zero signer");
            require(!isSigner[s], "MultiSigAdmin: duplicate signer");
            isSigner[s] = true;
            signers.push(s);
        }
        threshold = _threshold;
    }

    // ─────────────────────── API ───────────────────────────

    /// @notice Queue a new admin call. Only signers can propose.
    /// @param _target Target contract (e.g. EmissionRegistry proxy).
    /// @param _data   ABI-encoded calldata for the target.
    /// @param _value  Native currency to forward (usually 0).
    /// @return id     The new proposal id.
    function propose(address _target, bytes calldata _data, uint256 _value)
        external
        onlySigner
        returns (uint256 id)
    {
        id = proposalCount++;
        Proposal storage p = _proposals[id];
        p.target = _target;
        p.data = _data;
        p.value = _value;
        emit Proposed(id, msg.sender, _target, _data, _value);
        // The proposer implicitly confirms. Use an inline version of
        // _confirm so we don't need to re-check proposalExists.
        p.confirmedBy[msg.sender] = true;
        p.confirmations = 1;
        emit Confirmed(id, msg.sender, 1);
    }

    /// @notice Add a confirmation to an existing proposal.
    function confirm(uint256 _id) external onlySigner proposalExists(_id) {
        Proposal storage p = _proposals[_id];
        if (p.executed) revert AlreadyExecuted();
        if (p.confirmedBy[msg.sender]) revert AlreadyConfirmed();
        p.confirmedBy[msg.sender] = true;
        p.confirmations += 1;
        emit Confirmed(_id, msg.sender, p.confirmations);
    }

    /// @notice Revoke a previously-granted confirmation. Only possible
    ///         before execution.
    function revoke(uint256 _id) external onlySigner proposalExists(_id) {
        Proposal storage p = _proposals[_id];
        if (p.executed) revert AlreadyExecuted();
        if (!p.confirmedBy[msg.sender]) revert NotConfirmed();
        p.confirmedBy[msg.sender] = false;
        p.confirmations -= 1;
        emit Revoked(_id, msg.sender);
    }

    /// @notice Execute a proposal once it has enough confirmations.
    ///         Any signer can invoke this; the on-chain effect is
    ///         identical regardless of who calls.
    function execute(uint256 _id) external onlySigner proposalExists(_id) {
        Proposal storage p = _proposals[_id];
        if (p.executed) revert AlreadyExecuted();
        if (p.confirmations < threshold) revert BelowThreshold();
        p.executed = true;
        (bool ok, ) = p.target.call{value: p.value}(p.data);
        if (!ok) revert ExecutionFailed();
        emit Executed(_id, msg.sender, ok);
    }

    // ─────────────────────── Views ─────────────────────────

    /// @notice Return a flattened view of a proposal (mapping field omitted).
    function getProposal(uint256 _id)
        external
        view
        proposalExists(_id)
        returns (address target, bytes memory data, uint256 value, bool executed, uint256 confirmations)
    {
        Proposal storage p = _proposals[_id];
        return (p.target, p.data, p.value, p.executed, p.confirmations);
    }

    /// @notice Whether a specific signer has confirmed a specific proposal.
    function hasConfirmed(uint256 _id, address _signer) external view returns (bool) {
        return _proposals[_id].confirmedBy[_signer];
    }

    /// @notice Number of configured signers.
    function signerCount() external view returns (uint256) {
        return signers.length;
    }

    // Allow the multisig to receive native currency so proposals can
    // forward a non-zero `value` (e.g. to fund a faucet contract).
    receive() external payable {}
}
