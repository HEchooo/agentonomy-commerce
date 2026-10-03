// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.24;

/// @title Agentonomy Budget Executor
/// @notice Executes user-signed, fixed-recipient ERC-20 purchases under a bounded grant.
/// @dev The grant is a user authorization; the execution is a separate Core-authorized
///      permit. This contract has no upgrade, admin, or arbitrary-call surface.
contract AgentonomyBudgetExecutor {
    string public constant EIP712_NAME = "Agentonomy Budget Executor";
    string public constant EIP712_VERSION = "1";

    bytes32 public constant EIP712_DOMAIN_TYPEHASH =
        keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)");
    bytes32 public constant SPEND_GRANT_TYPEHASH = keccak256(
        "SpendGrant(bytes32 grantId,address owner,bytes32 agentScope,address token,address payee,uint256 maxPerPayment,uint256 maxTotal,uint256 validAfter,uint256 validUntil,address executionSigner)"
    );
    bytes32 public constant PURCHASE_EXECUTION_TYPEHASH = keccak256(
        "PurchaseExecution(bytes32 grantHash,bytes32 purchaseId,bytes32 quoteHash,uint256 amount,uint256 deadline)"
    );

    uint256 private constant _SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141;
    uint256 private constant _SECP256K1_HALF_N = 0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0;
    uint256 private constant _NOT_ENTERED = 1;
    uint256 private constant _ENTERED = 2;

    struct SpendGrant {
        bytes32 grantId;
        address owner;
        bytes32 agentScope;
        address token;
        address payee;
        uint256 maxPerPayment;
        uint256 maxTotal;
        uint256 validAfter;
        uint256 validUntil;
        address executionSigner;
    }

    struct PurchaseExecution {
        bytes32 grantHash;
        bytes32 purchaseId;
        bytes32 quoteHash;
        uint256 amount;
        uint256 deadline;
    }

    error InvalidGrant();
    error InvalidExecution();
    error InvalidSignature();
    error WrongChain();
    error GrantRevokedError();
    error GrantConflict();
    error GrantNotActive();
    error GrantExpired();
    error ExecutionExpired();
    error PerPaymentExceeded();
    error TotalExceeded();
    error PurchaseAlreadyPaid();
    error TransferFailed();
    error ReentrantCall();

    event PaymentExecuted(
        bytes32 indexed grantHash,
        bytes32 indexed purchaseId,
        address indexed owner,
        bytes32 grantId,
        bytes32 quoteHash,
        address token,
        address payee,
        uint256 amount
    );
    event GrantRevoked(address indexed owner, bytes32 indexed grantId);

    address public immutable TOKEN;
    uint256 public immutable EXECUTION_CHAIN_ID;
    bytes32 public immutable DOMAIN_SEPARATOR;

    mapping(address owner => mapping(bytes32 grantId => bytes32 grantHash)) public grantDigests;
    mapping(bytes32 grantHash => uint256 amount) public spent;
    mapping(address owner => mapping(bytes32 grantId => bool isRevoked)) public revoked;
    mapping(address owner => mapping(bytes32 purchaseId => bool isPaid)) public paid;

    uint256 private _reentrancyStatus = _NOT_ENTERED;

    constructor(address tokenAddress) {
        if (tokenAddress == address(0) || tokenAddress.code.length == 0) revert InvalidGrant();

        TOKEN = tokenAddress;
        EXECUTION_CHAIN_ID = block.chainid;
        DOMAIN_SEPARATOR = keccak256(
            abi.encode(
                EIP712_DOMAIN_TYPEHASH,
                keccak256(bytes(EIP712_NAME)),
                keccak256(bytes(EIP712_VERSION)),
                EXECUTION_CHAIN_ID,
                address(this)
            )
        );
    }

    modifier nonReentrant() {
        if (_reentrancyStatus == _ENTERED) revert ReentrantCall();
        _reentrancyStatus = _ENTERED;
        _;
        _reentrancyStatus = _NOT_ENTERED;
    }

    /// @notice Alias for integrations that use lower-case field-style getters.
    function token() external view returns (address) {
        return TOKEN;
    }

    /// @notice Alias for integrations that use lower-case field-style getters.
    function chainId() external view returns (uint256) {
        return EXECUTION_CHAIN_ID;
    }

    function hashGrant(SpendGrant calldata grant) public view returns (bytes32) {
        bytes32 structHash = keccak256(
            abi.encode(
                SPEND_GRANT_TYPEHASH,
                grant.grantId,
                grant.owner,
                grant.agentScope,
                grant.token,
                grant.payee,
                grant.maxPerPayment,
                grant.maxTotal,
                grant.validAfter,
                grant.validUntil,
                grant.executionSigner
            )
        );
        return keccak256(abi.encodePacked("\x19\x01", DOMAIN_SEPARATOR, structHash));
    }

    function hashExecution(PurchaseExecution calldata execution) public view returns (bytes32) {
        bytes32 structHash = keccak256(
            abi.encode(
                PURCHASE_EXECUTION_TYPEHASH,
                execution.grantHash,
                execution.purchaseId,
                execution.quoteHash,
                execution.amount,
                execution.deadline
            )
        );
        return keccak256(abi.encodePacked("\x19\x01", DOMAIN_SEPARATOR, structHash));
    }

    function revoke(bytes32 grantId) external {
        if (grantId == bytes32(0)) revert InvalidGrant();
        revoked[msg.sender][grantId] = true;
        emit GrantRevoked(msg.sender, grantId);
    }

    function execute(
        SpendGrant calldata grant,
        bytes calldata ownerSignature,
        PurchaseExecution calldata execution,
        bytes calldata executionSignature
    ) external nonReentrant {
        if (block.chainid != EXECUTION_CHAIN_ID) revert WrongChain();
        _validateGrant(grant);
        _validateExecution(grant, execution);

        bytes32 grantHash = hashGrant(grant);
        if (execution.grantHash != grantHash) revert InvalidExecution();
        if (revoked[grant.owner][grant.grantId]) revert GrantRevokedError();

        bytes32 pinnedGrantHash = grantDigests[grant.owner][grant.grantId];
        if (pinnedGrantHash != bytes32(0) && pinnedGrantHash != grantHash) {
            revert GrantConflict();
        }
        if (paid[grant.owner][execution.purchaseId]) revert PurchaseAlreadyPaid();

        uint256 previousSpent = spent[grantHash];
        if (previousSpent > grant.maxTotal || execution.amount > grant.maxTotal - previousSpent) {
            revert TotalExceeded();
        }

        if (_recover(grantHash, ownerSignature) != grant.owner) revert InvalidSignature();
        if (_recover(hashExecution(execution), executionSignature) != grant.executionSigner) {
            revert InvalidSignature();
        }

        // Pin every replay-sensitive effect before interacting with the token. A token
        // call failure reverts these writes together with all token state changes.
        if (pinnedGrantHash == bytes32(0)) {
            grantDigests[grant.owner][grant.grantId] = grantHash;
        }
        spent[grantHash] = previousSpent + execution.amount;
        paid[grant.owner][execution.purchaseId] = true;

        _transferExact(grant.owner, grant.payee, execution.amount);

        emit PaymentExecuted(
            grantHash,
            execution.purchaseId,
            grant.owner,
            grant.grantId,
            execution.quoteHash,
            grant.token,
            grant.payee,
            execution.amount
        );
    }

    function _validateGrant(SpendGrant calldata grant) internal view {
        if (
            grant.grantId == bytes32(0) || grant.owner == address(0) || grant.agentScope == bytes32(0)
                || grant.token != TOKEN || grant.payee == address(0) || grant.owner == grant.payee
                || grant.executionSigner == address(0) || grant.maxPerPayment == 0 || grant.maxTotal == 0
                || grant.maxPerPayment > grant.maxTotal || grant.validAfter >= grant.validUntil
        ) {
            revert InvalidGrant();
        }
        if (block.timestamp < grant.validAfter) revert GrantNotActive();
        if (block.timestamp >= grant.validUntil) revert GrantExpired();
    }

    function _validateExecution(SpendGrant calldata grant, PurchaseExecution calldata execution) internal view {
        if (
            execution.grantHash == bytes32(0) || execution.purchaseId == bytes32(0) || execution.quoteHash == bytes32(0)
                || execution.amount == 0 || execution.deadline > grant.validUntil
        ) {
            revert InvalidExecution();
        }
        if (block.timestamp >= execution.deadline) revert ExecutionExpired();
        if (execution.amount > grant.maxPerPayment) revert PerPaymentExceeded();
    }

    function _transferExact(address from, address to, uint256 amount) internal {
        if (TOKEN.code.length == 0) revert TransferFailed();

        uint256 ownerBefore = _balanceOf(from);
        uint256 payeeBefore = _balanceOf(to);
        (bool success, bytes memory returnData) = TOKEN.call(
            abi.encodeWithSelector(bytes4(keccak256("transferFrom(address,address,uint256)")), from, to, amount)
        );
        if (!success || returnData.length != 32 || abi.decode(returnData, (uint256)) != 1) {
            revert TransferFailed();
        }

        uint256 ownerAfter = _balanceOf(from);
        uint256 payeeAfter = _balanceOf(to);
        if (
            ownerAfter > ownerBefore || ownerBefore - ownerAfter != amount || payeeAfter < payeeBefore
                || payeeAfter - payeeBefore != amount
        ) {
            revert TransferFailed();
        }
    }

    function _balanceOf(address account) internal view returns (uint256 balance) {
        (bool success, bytes memory returnData) =
            TOKEN.staticcall(abi.encodeWithSelector(bytes4(keccak256("balanceOf(address)")), account));
        if (!success || returnData.length != 32) revert TransferFailed();
        balance = abi.decode(returnData, (uint256));
    }

    function _recover(bytes32 digest, bytes calldata signature) internal pure returns (address recovered) {
        if (signature.length != 65) revert InvalidSignature();

        bytes32 r;
        bytes32 s;
        uint8 v;
        assembly {
            r := calldataload(signature.offset)
            s := calldataload(add(signature.offset, 32))
            v := byte(0, calldataload(add(signature.offset, 64)))
        }
        if (v != 27 && v != 28) revert InvalidSignature();
        if (uint256(r) == 0 || uint256(r) >= _SECP256K1_N) revert InvalidSignature();
        if (uint256(s) == 0 || uint256(s) > _SECP256K1_HALF_N) revert InvalidSignature();

        recovered = ecrecover(digest, v, r, s);
        if (recovered == address(0)) revert InvalidSignature();
    }
}
