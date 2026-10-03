// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.24;

interface BudgetInvariantVm {
    function addr(uint256 privateKey) external returns (address);
    function chainId(uint256 newChainId) external;
    function prank(address sender) external;
    function sign(uint256 privateKey, bytes32 digest) external returns (uint8 v, bytes32 r, bytes32 s);
    function warp(uint256 newTimestamp) external;
}

// Minimal local copy of forge-std's invariant configuration surface. The
// repository deliberately has no forge-std dependency, so this keeps handler
// targeting explicit without treating targetContract as a hevm cheat code.
abstract contract StdInvariant {
    struct FuzzSelector {
        address addr;
        bytes4[] selectors;
    }

    address[] private _excludedContracts;
    address[] private _targetedContracts;

    function excludeContract(address target) internal {
        _excludedContracts.push(target);
    }

    function targetContract(address target) internal {
        _targetedContracts.push(target);
    }

    function targetContracts() public view returns (address[] memory) {
        return _targetedContracts;
    }

    function excludeContracts() public view returns (address[] memory) {
        return _excludedContracts;
    }
}

import {AgentonomyBudgetExecutor} from "../src/AgentonomyBudgetExecutor.sol";
import {BudgetTestUSD} from "./mocks/BudgetTestUSD.sol";

/// @notice Stateful action handler that creates valid two-signature executions.
contract BudgetExecutorInvariantHandler {
    BudgetInvariantVm internal constant vm = BudgetInvariantVm(address(uint160(uint256(keccak256("hevm cheat code")))));

    uint256 internal constant OWNER_KEY = 0xA11CE;
    uint256 internal constant EXECUTION_SIGNER_KEY = 0xB0B;
    uint256 internal constant MAX_PER_PAYMENT = 100;
    uint256 internal constant MAX_TOTAL = 1_000_000_000;
    bytes32 internal constant GRANT_ID = bytes32(uint256(1));
    bytes32 internal constant AGENT_SCOPE = bytes32(uint256(2));

    AgentonomyBudgetExecutor public immutable executor;
    BudgetTestUSD public immutable token;
    address public immutable owner;
    address public immutable signer;
    address public immutable payee;
    bytes32 public immutable grantHash;

    uint256 public expectedSpent;
    uint256 public successfulExecutions;
    uint256 public replayAttempts;
    uint256 public rollbackChecks;
    uint256 public revokeBlockedAttempts;
    uint256 public nextPurchaseId;
    bytes32 public lastPurchaseId;
    bool public grantRevoked;

    constructor(AgentonomyBudgetExecutor executor_, BudgetTestUSD token_, address payee_) {
        executor = executor_;
        token = token_;
        owner = vm.addr(OWNER_KEY);
        signer = vm.addr(EXECUTION_SIGNER_KEY);
        payee = payee_;
        grantHash = executor_.hashGrant(_grant());
    }

    function seedInitial() external {
        if (successfulExecutions == 0) executeValid(1, 1);
    }

    function executeValid(uint256 amountSeed, uint256 quoteSeed) public {
        if (grantRevoked || expectedSpent >= MAX_TOTAL) return;
        uint256 amount = amountSeed % MAX_PER_PAYMENT + 1;
        if (amount > MAX_TOTAL - expectedSpent) return;

        bytes32 purchaseId = bytes32(++nextPurchaseId);
        AgentonomyBudgetExecutor.SpendGrant memory grant = _grant();
        AgentonomyBudgetExecutor.PurchaseExecution memory execution =
            _execution(grant, purchaseId, bytes32(quoteSeed), amount);
        executor.execute(grant, _sign(OWNER_KEY, grantHash), execution, _signExecution(execution));

        expectedSpent += amount;
        successfulExecutions += 1;
        lastPurchaseId = purchaseId;
    }

    function replayLast() external {
        if (successfulExecutions == 0) return;
        AgentonomyBudgetExecutor.SpendGrant memory grant = _grant();
        AgentonomyBudgetExecutor.PurchaseExecution memory execution =
            _execution(grant, lastPurchaseId, bytes32(uint256(lastPurchaseId)), 1);
        (bool success,) = address(executor)
            .call(
                abi.encodeWithSelector(
                    AgentonomyBudgetExecutor.execute.selector,
                    grant,
                    _sign(OWNER_KEY, grantHash),
                    execution,
                    _signExecution(execution)
                )
            );
        require(!success, "purchase replay unexpectedly succeeded");
        require(executor.spent(grantHash) == expectedSpent, "replay changed spent");
        require(executor.paid(owner, lastPurchaseId), "replay lost paid marker");
        replayAttempts += 1;
    }

    function failedTransfer(uint256 quoteSeed) external {
        if (grantRevoked || expectedSpent >= MAX_TOTAL) return;
        uint256 amount = 7;
        if (amount > MAX_TOTAL - expectedSpent) return;

        bytes32 purchaseId = bytes32(++nextPurchaseId);
        AgentonomyBudgetExecutor.SpendGrant memory grant = _grant();
        AgentonomyBudgetExecutor.PurchaseExecution memory execution =
            _execution(grant, purchaseId, bytes32(quoteSeed), amount);
        uint256 spentBefore = executor.spent(grantHash);
        bool paidBefore = executor.paid(owner, purchaseId);
        bytes32 pinnedBefore = executor.grantDigests(owner, GRANT_ID);
        uint256 ownerBefore = token.balanceOf(owner);
        uint256 payeeBefore = token.balanceOf(payee);
        uint256 allowanceBefore = token.allowance(owner, address(executor));

        token.configure(address(executor), BudgetTestUSD.Mode.FeeOnTransfer);
        (bool success,) = address(executor)
            .call(
                abi.encodeWithSelector(
                    AgentonomyBudgetExecutor.execute.selector,
                    grant,
                    _sign(OWNER_KEY, grantHash),
                    execution,
                    _signExecution(execution)
                )
            );
        token.configure(address(executor), BudgetTestUSD.Mode.Success);
        require(!success, "fee token unexpectedly succeeded");
        require(executor.spent(grantHash) == spentBefore, "failed transfer changed spent");
        require(executor.paid(owner, purchaseId) == paidBefore, "failed transfer changed paid");
        require(executor.grantDigests(owner, GRANT_ID) == pinnedBefore, "failed transfer changed pin");
        require(token.balanceOf(owner) == ownerBefore, "failed transfer changed owner balance");
        require(token.balanceOf(payee) == payeeBefore, "failed transfer changed payee balance");
        require(token.allowance(owner, address(executor)) == allowanceBefore, "failed transfer changed allowance");
        rollbackChecks += 1;
    }

    function revokeGrant() external {
        if (grantRevoked || successfulExecutions < 2 || replayAttempts == 0 || rollbackChecks == 0) return;
        vm.prank(owner);
        executor.revoke(GRANT_ID);
        grantRevoked = true;
    }

    function attemptAfterRevoke(uint256 quoteSeed) external {
        if (!grantRevoked) return;
        bytes32 purchaseId = bytes32(++nextPurchaseId);
        AgentonomyBudgetExecutor.SpendGrant memory grant = _grant();
        AgentonomyBudgetExecutor.PurchaseExecution memory execution =
            _execution(grant, purchaseId, bytes32(quoteSeed), 1);
        (bool success,) = address(executor)
            .call(
                abi.encodeWithSelector(
                    AgentonomyBudgetExecutor.execute.selector,
                    grant,
                    _sign(OWNER_KEY, grantHash),
                    execution,
                    _signExecution(execution)
                )
            );
        require(!success, "revoked grant unexpectedly succeeded");
        require(!executor.paid(owner, purchaseId), "revoked purchase was paid");
        require(executor.spent(grantHash) == expectedSpent, "revocation changed spent");
        revokeBlockedAttempts += 1;
    }

    function _grant() internal view returns (AgentonomyBudgetExecutor.SpendGrant memory grant) {
        grant = AgentonomyBudgetExecutor.SpendGrant({
            grantId: GRANT_ID,
            owner: owner,
            agentScope: AGENT_SCOPE,
            token: address(token),
            payee: payee,
            maxPerPayment: MAX_PER_PAYMENT,
            maxTotal: MAX_TOTAL,
            validAfter: 0,
            validUntil: type(uint256).max,
            executionSigner: signer
        });
    }

    function _execution(
        AgentonomyBudgetExecutor.SpendGrant memory grant,
        bytes32 purchaseId,
        bytes32 quoteHash,
        uint256 amount
    ) internal view returns (AgentonomyBudgetExecutor.PurchaseExecution memory execution) {
        execution = AgentonomyBudgetExecutor.PurchaseExecution({
                grantHash: executor.hashGrant(grant),
                purchaseId: purchaseId,
                quoteHash: quoteHash == bytes32(0) ? bytes32(uint256(1)) : quoteHash,
                amount: amount,
                deadline: type(uint256).max
            });
    }

    function _sign(uint256 privateKey, bytes32 digest) internal returns (bytes memory signature) {
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(privateKey, digest);
        signature = abi.encodePacked(r, s, v);
    }

    function _signExecution(AgentonomyBudgetExecutor.PurchaseExecution memory execution)
        internal
        returns (bytes memory)
    {
        return _sign(EXECUTION_SIGNER_KEY, executor.hashExecution(execution));
    }
}

/// @notice Stateful invariant suite with handler-only fuzz targets.
contract AgentonomyBudgetExecutorInvariantTest is StdInvariant {
    BudgetInvariantVm internal constant vm = BudgetInvariantVm(address(uint160(uint256(keccak256("hevm cheat code")))));

    uint256 internal constant OWNER_KEY = 0xA11CE;
    uint256 internal constant DEPLOYMENT_CHAIN_ID = 31_337;
    address internal owner;
    address internal payee = address(0xBEEF);
    BudgetTestUSD internal token;
    AgentonomyBudgetExecutor internal executor;
    BudgetExecutorInvariantHandler internal handler;
    bytes32 internal grantHash;

    function setUp() public {
        vm.chainId(DEPLOYMENT_CHAIN_ID);
        vm.warp(100);
        owner = vm.addr(OWNER_KEY);
        token = new BudgetTestUSD();
        executor = new AgentonomyBudgetExecutor(address(token));
        token.configure(address(executor), BudgetTestUSD.Mode.Success);
        token.mint(owner, 1_000_000_000_000);
        vm.prank(owner);
        token.approve(address(executor), type(uint256).max);
        handler = new BudgetExecutorInvariantHandler(executor, token, payee);
        handler.seedInitial();
        // The repository's current Foundry profile may run zero random calls;
        // exercise every guarded path once during setup so this invariant is
        // still meaningful in that mode. These are the same handler actions
        // exposed to invariant fuzzing when runs/depth are enabled.
        handler.executeValid(2, 2);
        handler.replayLast();
        handler.failedTransfer(3);
        handler.revokeGrant();
        handler.attemptAfterRevoke(4);
        grantHash = handler.grantHash();
        targetContract(address(handler));
        excludeContract(address(executor));
        excludeContract(address(token));
    }

    function invariant_successfulCallsExerciseAccountingAndReplayGuards() public view {
        require(handler.successfulExecutions() > 0, "no successful handler execution");
        require(executor.spent(grantHash) == handler.expectedSpent(), "spent accounting mismatch");
        require(executor.spent(grantHash) <= 1_000_000_000, "spent exceeds total");
        require(handler.replayAttempts() > 0, "replay action was never exercised");
        require(handler.rollbackChecks() > 0, "rollback action was never exercised");
        require(executor.grantDigests(handler.owner(), bytes32(uint256(1))) == grantHash, "grant pin mismatch");
        if (handler.grantRevoked()) {
            require(executor.revoked(handler.owner(), bytes32(uint256(1))), "revoke marker missing");
            require(handler.revokeBlockedAttempts() > 0, "post-revoke action was never exercised");
        }
    }
}
