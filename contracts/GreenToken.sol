// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";

/**
 * @title GreenToken
 * @author Smart PUC Team
 * @notice ERC-20 reward token for emission-compliant vehicles.
 * @dev Vehicles that receive a PUC certificate are awarded Green Credit Tokens (GCT)
 *      as an incentive for maintaining low emissions. Tokens can be redeemed for
 *      benefits like toll discounts, parking fee waivers, or tax credits.
 *
 *      Only authorized minters (PUCCertificate contract) can create new tokens.
 *      The admin manages minter authorization.
 *
 *      The redemption marketplace allows users to burn tokens in exchange for
 *      real-world rewards. Each redemption is tracked on-chain with a unique ID.
 */
contract GreenToken is ERC20 {

    // ───────────────────────── Reward Types ───────────────────────────

    uint8 public constant TOLL_DISCOUNT    = 0;
    uint8 public constant PARKING_WAIVER   = 1;
    uint8 public constant TAX_CREDIT       = 2;
    uint8 public constant PRIORITY_SERVICE = 3;
    uint8 public constant REWARD_TYPE_COUNT = 4;

    // ───────────────────────── Reward Costs ───────────────────────────

    mapping(uint8 => uint256) public rewardCost;

    // ───────────────────────── Redemption Record ──────────────────────

    struct Redemption {
        address user;
        uint8   rewardType;
        uint256 amount;
        uint256 timestamp;
    }

    /// @notice Contract administrator
    address public admin;

    /// @notice Addresses authorized to mint tokens (e.g., PUCCertificate contract)
    mapping(address => bool) public authorizedMinters;

    /// @notice Total tokens minted as rewards (tracking metric)
    uint256 public totalRewardsMinted;

    /// @notice Per-address reward tracking
    mapping(address => uint256) public rewardsEarned;

    /// @notice Auto-incrementing redemption ID (next ID to assign)
    uint256 public nextRedemptionId;

    /// @notice Redemption records by ID
    mapping(uint256 => Redemption) public redemptions;

    /// @notice Total redemptions per address
    mapping(address => uint256) public redemptionCount;

    /// @notice Redemptions per address per reward type
    mapping(address => mapping(uint8 => uint256)) public redemptionsByType;

    /// @notice Total tokens burned through redemptions
    uint256 public totalRedeemed;

    // ───────────────────────── Events ─────────────────────────────────

    event MinterUpdated(address indexed minter, bool authorized);
    event RewardMinted(address indexed recipient, uint256 amount, uint256 totalEarned);
    event Redeemed(address indexed user, uint8 rewardType, uint256 amount, uint256 redemptionId);

    // ───────────────────────── Constructor ─────────────────────────────

    constructor() ERC20("Green Credit Token", "GCT") {
        admin = msg.sender;

        // Initialize reward costs
        rewardCost[TOLL_DISCOUNT]    = 50 * 10 ** 18;
        rewardCost[PARKING_WAIVER]   = 30 * 10 ** 18;
        rewardCost[TAX_CREDIT]       = 100 * 10 ** 18;
        rewardCost[PRIORITY_SERVICE] = 20 * 10 ** 18;
    }

    // ───────────────────────── Admin Functions ─────────────────────────

    /// @notice Authorize or deauthorize a minter address
    function setMinter(address _minter, bool _authorized) external {
        require(msg.sender == admin, "Only admin can manage minters");
        authorizedMinters[_minter] = _authorized;
        emit MinterUpdated(_minter, _authorized);
    }

    /// @notice Transfer admin role
    function transferAdmin(address _newAdmin) external {
        require(msg.sender == admin, "Only admin");
        require(_newAdmin != address(0), "Invalid address");
        admin = _newAdmin;
    }

    // ───────────────────────── Minting ─────────────────────────────────

    /**
     * @notice Mint reward tokens to a vehicle owner.
     * @dev Only callable by authorized minters (PUCCertificate contract).
     * @param _to     Recipient address (vehicle owner)
     * @param _amount Number of tokens to mint (in wei, 18 decimals)
     */
    function mint(address _to, uint256 _amount) external {
        require(authorizedMinters[msg.sender], "Not authorized to mint");
        require(_to != address(0), "Cannot mint to zero address");
        require(_amount > 0, "Amount must be greater than zero");

        _mint(_to, _amount);
        totalRewardsMinted += _amount;
        rewardsEarned[_to] += _amount;

        emit RewardMinted(_to, _amount, rewardsEarned[_to]);
    }

    // ───────────────────────── Redemption / Burn ──────────────────────

    /**
     * @notice Redeem (burn) tokens for a reward.
     * @param _rewardType The type of reward to redeem (0-3)
     * @return redemptionId The unique receipt ID for this redemption
     */
    function redeem(uint8 _rewardType) external returns (uint256 redemptionId) {
        require(_rewardType < REWARD_TYPE_COUNT, "Invalid reward type");

        uint256 cost = rewardCost[_rewardType];
        require(balanceOf(msg.sender) >= cost, "Insufficient GCT balance");

        // Burn the tokens
        _burn(msg.sender, cost);

        // Assign redemption ID
        redemptionId = nextRedemptionId;
        nextRedemptionId++;

        // Store redemption record
        redemptions[redemptionId] = Redemption({
            user: msg.sender,
            rewardType: _rewardType,
            amount: cost,
            timestamp: block.timestamp
        });

        // Update tracking
        redemptionCount[msg.sender]++;
        redemptionsByType[msg.sender][_rewardType]++;
        totalRedeemed += cost;

        emit Redeemed(msg.sender, _rewardType, cost, redemptionId);
    }

    // ───────────────────────── View Functions ──────────────────────────

    /// @notice Get reward summary for an address
    function getRewardSummary(address _account)
        external view returns (uint256 balance, uint256 earned)
    {
        balance = balanceOf(_account);
        earned = rewardsEarned[_account];
    }

    /// @notice Get the cost of a reward type
    function getRewardCost(uint8 _rewardType) external view returns (uint256) {
        require(_rewardType < REWARD_TYPE_COUNT, "Invalid reward type");
        return rewardCost[_rewardType];
    }

    /// @notice Get a redemption record by ID
    function getRedemption(uint256 _id)
        external view returns (address user, uint8 rewardType, uint256 amount, uint256 timestamp)
    {
        require(_id < nextRedemptionId, "Redemption does not exist");
        Redemption storage r = redemptions[_id];
        return (r.user, r.rewardType, r.amount, r.timestamp);
    }

    /// @notice Get redemption stats for an address
    function getRedemptionStats(address _account)
        external view returns (
            uint256 totalCount,
            uint256 tollDiscounts,
            uint256 parkingWaivers,
            uint256 taxCredits,
            uint256 priorityServices
        )
    {
        totalCount       = redemptionCount[_account];
        tollDiscounts    = redemptionsByType[_account][TOLL_DISCOUNT];
        parkingWaivers   = redemptionsByType[_account][PARKING_WAIVER];
        taxCredits       = redemptionsByType[_account][TAX_CREDIT];
        priorityServices = redemptionsByType[_account][PRIORITY_SERVICE];
    }
}
