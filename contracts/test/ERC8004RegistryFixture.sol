// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.24;

/// @dev Test-only Identity Registry ABI fixture. This is not an official deployment.
/// The constructor-pinned wallet intentionally simplifies local receiving-wallet binding.
contract ERC8004IdentityRegistryFixture {
    uint256 private _nextAgentId;
    address public immutable defaultWallet;
    mapping(uint256 => address) private _owners;
    mapping(uint256 => address) private _agentWallets;
    mapping(uint256 => string) private _agentUris;

    constructor(address wallet) {
        require(wallet != address(0), "wallet required");
        defaultWallet = wallet;
    }

    function register(string calldata agentURI) external returns (uint256 agentId) {
        agentId = _nextAgentId;
        _nextAgentId = agentId + 1;
        _owners[agentId] = msg.sender;
        _agentWallets[agentId] = defaultWallet;
        _agentUris[agentId] = agentURI;
    }

    function ownerOf(uint256 agentId) external view returns (address owner) {
        owner = _owners[agentId];
        require(owner != address(0), "unknown agent");
    }

    function getAgentWallet(uint256 agentId) external view returns (address wallet) {
        wallet = _agentWallets[agentId];
        require(wallet != address(0), "unknown agent");
    }

    function tokenURI(uint256 agentId) external view returns (string memory) {
        require(_owners[agentId] != address(0), "unknown agent");
        return _agentUris[agentId];
    }

    function isAuthorizedOrOwner(address user, uint256 agentId) external view returns (bool) {
        address owner = _owners[agentId];
        require(owner != address(0), "unknown agent");
        return owner == user;
    }
}

interface IERC8004IdentityFixture {
    function isAuthorizedOrOwner(address user, uint256 agentId) external view returns (bool);
}

/// @dev Test-only Reputation Registry ABI/event fixture. This is not an official deployment.
contract ERC8004ReputationRegistryFixture {
    struct Feedback {
        int128 value;
        uint8 valueDecimals;
        string tag1;
        string tag2;
        string endpoint;
        string feedbackURI;
        bytes32 feedbackHash;
        bool revoked;
    }

    address public immutable identityRegistry;
    mapping(uint256 => mapping(address => Feedback[])) private _feedback;

    event NewFeedback(
        uint256 indexed agentId,
        address indexed clientAddress,
        uint64 feedbackIndex,
        int128 value,
        uint8 valueDecimals,
        string indexed indexedTag1,
        string tag1,
        string tag2,
        string endpoint,
        string feedbackURI,
        bytes32 feedbackHash
    );

    constructor(address identity) {
        require(identity != address(0), "identity required");
        identityRegistry = identity;
    }

    function giveFeedback(
        uint256 agentId,
        int128 value,
        uint8 valueDecimals,
        string calldata tag1,
        string calldata tag2,
        string calldata endpoint,
        string calldata feedbackURI,
        bytes32 feedbackHash
    ) external {
        require(value >= 0 && value <= 100, "score out of range");
        require(
            !IERC8004IdentityFixture(identityRegistry).isAuthorizedOrOwner(msg.sender, agentId), "owner or authorized"
        );
        Feedback storage feedback = _feedback[agentId][msg.sender].push();
        feedback.value = value;
        feedback.valueDecimals = valueDecimals;
        feedback.tag1 = tag1;
        feedback.tag2 = tag2;
        feedback.endpoint = endpoint;
        feedback.feedbackURI = feedbackURI;
        feedback.feedbackHash = feedbackHash;
        uint64 feedbackIndex = uint64(_feedback[agentId][msg.sender].length);
        _emitFeedback(agentId, msg.sender, feedbackIndex);
    }

    function _emitFeedback(uint256 agentId, address clientAddress, uint64 feedbackIndex) internal {
        Feedback storage feedback = _feedback[agentId][clientAddress][feedbackIndex - 1];
        emit NewFeedback(
            agentId,
            clientAddress,
            feedbackIndex,
            feedback.value,
            feedback.valueDecimals,
            feedback.tag1,
            feedback.tag1,
            feedback.tag2,
            feedback.endpoint,
            feedback.feedbackURI,
            feedback.feedbackHash
        );
    }

    function getIdentityRegistry() external view returns (address) {
        return identityRegistry;
    }

    function readFeedback(uint256 agentId, address clientAddress, uint64 feedbackIndex)
        external
        view
        returns (int128, uint8, string memory, string memory, bool)
    {
        require(feedbackIndex > 0, "feedback index required");
        Feedback storage feedback = _feedback[agentId][clientAddress][feedbackIndex - 1];
        require(bytes(feedback.tag1).length != 0, "unknown feedback");
        return (feedback.value, feedback.valueDecimals, feedback.tag1, feedback.tag2, feedback.revoked);
    }

    function revokeFeedback(uint256 agentId, uint64 feedbackIndex) external {
        address clientAddress = msg.sender;
        require(feedbackIndex > 0, "feedback index required");
        Feedback storage feedback = _feedback[agentId][clientAddress][feedbackIndex - 1];
        require(bytes(feedback.tag1).length != 0, "unknown feedback");
        feedback.revoked = true;
    }
}
