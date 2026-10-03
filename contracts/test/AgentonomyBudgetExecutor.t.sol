// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.24;

interface BudgetVm {
    struct Log {
        bytes32[] topics;
        bytes data;
        address emitter;
    }

    function addr(uint256 privateKey) external returns (address);
    function assume(bool condition) external;
    function chainId(uint256 newChainId) external;
    function expectEmit(bool checkTopic1, bool checkTopic2, bool checkTopic3, bool checkData) external;
    function expectRevert(bytes4 selector) external;
    function getRecordedLogs() external returns (Log[] memory);
    function prank(address sender) external;
    function recordLogs() external;
    function sign(uint256 privateKey, bytes32 digest) external returns (uint8 v, bytes32 r, bytes32 s);
    function warp(uint256 newTimestamp) external;
}

import {AgentonomyBudgetExecutor} from "../src/AgentonomyBudgetExecutor.sol";
import {BudgetTestUSD} from "./mocks/BudgetTestUSD.sol";

contract AgentonomyBudgetExecutorTest {
    BudgetVm internal constant vm = BudgetVm(address(uint160(uint256(keccak256("hevm cheat code")))));

    uint256 internal constant OWNER_KEY = 0xA11CE;
    uint256 internal constant EXECUTION_SIGNER_KEY = 0xB0B;
    uint256 internal constant ATTACKER_KEY = 0xBAD;
    uint256 internal constant DEPLOYMENT_CHAIN_ID = 31_337;
    uint256 internal constant VALID_AFTER = 100;
    uint256 internal constant VALID_UNTIL = 10_000;
    uint256 internal constant EXECUTION_DEADLINE = 9_000;
    uint256 internal constant MAX_PER_PAYMENT = 100;
    uint256 internal constant MAX_TOTAL = 300;
    uint256 internal constant SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141;
    uint256 internal constant SECP256K1_HALF_N = 0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0;
    uint256 internal constant VECTOR_OWNER_KEY = 0x000102030405060708090A0B0C0D0E0F101112131415161718191A1B1C1D1E1F;
    uint256 internal constant VECTOR_EXECUTION_KEY = 0x1F1E1D1C1B1A191817161514131211100F0E0D0C0B0A09080706050403020100;
    bytes32 internal constant VECTOR_DOMAIN_TYPEHASH =
        0x8b73c3c69bb8fe3d512ecc4cf759cc79239f7b179b0ffacaa9a75d522b39400f;
    bytes32 internal constant VECTOR_GRANT_TYPEHASH =
        0x3987ffa2f5a43be976451c69817674958343a47fc9321412dd2c761ae3e04638;
    bytes32 internal constant VECTOR_EXECUTION_TYPEHASH =
        0x3caa9e033c0a4eebc364dd803fc523781652f4ec197cea573c952edb593e0244;

    address internal owner;
    address internal signer;
    address internal payee = address(0xBEEF);
    address internal attacker;
    BudgetTestUSD internal token;
    AgentonomyBudgetExecutor internal executor;

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

    function setUp() public {
        vm.chainId(DEPLOYMENT_CHAIN_ID);
        vm.warp(VALID_AFTER);
        owner = vm.addr(OWNER_KEY);
        signer = vm.addr(EXECUTION_SIGNER_KEY);
        attacker = vm.addr(ATTACKER_KEY);
        token = new BudgetTestUSD();
        executor = new AgentonomyBudgetExecutor(address(token));
        token.configure(address(executor), BudgetTestUSD.Mode.Success);
        token.mint(owner, 1_000_000);
        vm.prank(owner);
        token.approve(address(executor), type(uint256).max);
    }

    function test_executeTransfersExactlyAndEmitsFrozenEvent() public {
        AgentonomyBudgetExecutor.SpendGrant memory grant = _grant(1, MAX_PER_PAYMENT, MAX_TOTAL);
        AgentonomyBudgetExecutor.PurchaseExecution memory execution = _execution(grant, 1, 10, 75);
        bytes memory ownerSignature = _sign(OWNER_KEY, executor.hashGrant(grant));
        bytes memory executionSignature = _sign(EXECUTION_SIGNER_KEY, executor.hashExecution(execution));
        uint256 ownerBefore = token.balanceOf(owner);
        uint256 payeeBefore = token.balanceOf(payee);

        vm.recordLogs();
        executor.execute(grant, ownerSignature, execution, executionSignature);

        assertEq(token.balanceOf(owner), ownerBefore - execution.amount);
        assertEq(token.balanceOf(payee), payeeBefore + execution.amount);
        assertEq(executor.spent(execution.grantHash), execution.amount);
        assertTrue(executor.paid(owner, execution.purchaseId));
        assertEq(executor.grantDigests(owner, grant.grantId), execution.grantHash);
        _assertPaymentEvent(vm.getRecordedLogs(), execution, grant);
    }

    function test_hashesUseExactEip712TypeStringsAndFullDigest() public view {
        AgentonomyBudgetExecutor.SpendGrant memory grant = _grant(2, MAX_PER_PAYMENT, MAX_TOTAL);
        AgentonomyBudgetExecutor.PurchaseExecution memory execution = _execution(grant, 2, 20, 50);

        bytes32 expectedGrant = _literalGrantDigest(grant, DEPLOYMENT_CHAIN_ID, address(executor));
        bytes32 expectedExecution = _literalExecutionDigest(execution, DEPLOYMENT_CHAIN_ID, address(executor));

        assertEq(executor.hashGrant(grant), expectedGrant);
        assertEq(executor.hashExecution(execution), expectedExecution);
        assertEq(execution.grantHash, expectedGrant);
    }

    function test_sharedCrossLanguageVectorMatchesLiteralEip712Encoding() public {
        AgentonomyBudgetExecutor.SpendGrant memory grant = AgentonomyBudgetExecutor.SpendGrant({
            grantId: 0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa,
            owner: 0xedE35562d3555e61120a151B3c8e8e91d83a378a,
            agentScope: 0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb,
            token: 0x4444444444444444444444444444444444444444,
            payee: 0x2222222222222222222222222222222222222222,
            maxPerPayment: 300000000000000000,
            maxTotal: 1000000000000000000,
            validAfter: 1760000000,
            validUntil: 1760003600,
            executionSigner: 0xe70348acf619d425d8333F48C0b98bb64B9E5409
        });
        AgentonomyBudgetExecutor.PurchaseExecution memory execution = AgentonomyBudgetExecutor.PurchaseExecution({
            grantHash: 0xa1214c95ba06ace62c57e6d817b2e1a2af7e21f9a70adcf5bf14c25d6d10bdf8,
            purchaseId: 0xcccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc,
            quoteHash: 0xdddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd,
            amount: 250000000000000000,
            deadline: 1760000300
        });
        address vectorExecutor = 0x9999999999999999999999999999999999999999;
        bytes32 grantDigest = _literalGrantDigest(grant, 31337, vectorExecutor);
        bytes32 executionDigest = _literalExecutionDigest(execution, 31337, vectorExecutor);

        assertEq(grantDigest, 0xa1214c95ba06ace62c57e6d817b2e1a2af7e21f9a70adcf5bf14c25d6d10bdf8);
        assertEq(executionDigest, 0x67bb237bf937f671ccb5cf50abfac81a25942d31b2862061bb77ed08fe3d9961);

        (uint8 ownerV, bytes32 ownerR, bytes32 ownerS) = vm.sign(VECTOR_OWNER_KEY, grantDigest);
        (uint8 executionV, bytes32 executionR, bytes32 executionS) = vm.sign(VECTOR_EXECUTION_KEY, executionDigest);
        assertEq(
            keccak256(abi.encodePacked(ownerR, ownerS, ownerV)),
            keccak256(
                hex"dc072d2edfea7419846b721d03a838eb04dd71c0f8e643ebcfacb8f61568f2693daf0e89da6566d2627d61e0bc5c0e836e41360315f6d763272706dd1821778c1c"
            )
        );
        assertEq(
            keccak256(abi.encodePacked(executionR, executionS, executionV)),
            keccak256(
                hex"24cbc953e108ed1a05e6d4294d6832874f3d464e34bccfa28d8362df0c2ec76142184bfaad3edac104e881d6a26fdff3181ab532cd540d2ed6ffedfc4f7dd4061c"
            )
        );
    }

    function test_signaturesBindVerifyingContractAndChain() public {
        AgentonomyBudgetExecutor.SpendGrant memory grant = _grant(22, MAX_PER_PAYMENT, MAX_TOTAL);
        bytes memory ownerSignature = _sign(OWNER_KEY, executor.hashGrant(grant));

        AgentonomyBudgetExecutor otherExecutor = new AgentonomyBudgetExecutor(address(token));
        AgentonomyBudgetExecutor.PurchaseExecution memory otherExecution = _execution(grant, 220, 220, 25);
        otherExecution.grantHash = otherExecutor.hashGrant(grant);
        bytes memory otherExecutionSignature =
            _signDigest(EXECUTION_SIGNER_KEY, otherExecutor.hashExecution(otherExecution));
        vm.expectRevert(AgentonomyBudgetExecutor.InvalidSignature.selector);
        otherExecutor.execute(grant, ownerSignature, otherExecution, otherExecutionSignature);

        vm.chainId(DEPLOYMENT_CHAIN_ID + 1);
        AgentonomyBudgetExecutor chainExecutor = new AgentonomyBudgetExecutor(address(token));
        bytes memory otherChainOwnerSignature = _signDigest(OWNER_KEY, chainExecutor.hashGrant(grant));
        vm.chainId(DEPLOYMENT_CHAIN_ID);
        AgentonomyBudgetExecutor.PurchaseExecution memory originalAgain = _execution(grant, 220, 220, 25);
        bytes memory originalExecutionSignature = _signExecution(originalAgain);
        vm.expectRevert(AgentonomyBudgetExecutor.InvalidSignature.selector);
        executor.execute(grant, otherChainOwnerSignature, originalAgain, originalExecutionSignature);
    }

    function test_grantIdPinsBodyAndRejectsConflict() public {
        AgentonomyBudgetExecutor.SpendGrant memory first = _grant(3, MAX_PER_PAYMENT, MAX_TOTAL);
        AgentonomyBudgetExecutor.PurchaseExecution memory firstExecution = _execution(first, 3, 30, 50);
        executor.execute(
            first, _sign(OWNER_KEY, executor.hashGrant(first)), firstExecution, _signExecution(firstExecution)
        );

        AgentonomyBudgetExecutor.SpendGrant memory changed = _grant(3, MAX_PER_PAYMENT, MAX_TOTAL + 1);
        AgentonomyBudgetExecutor.PurchaseExecution memory changedExecution = _execution(changed, 4, 31, 50);
        bytes memory changedOwnerSignature = _sign(OWNER_KEY, executor.hashGrant(changed));
        bytes memory changedExecutionSignature = _signExecution(changedExecution);
        vm.expectRevert(AgentonomyBudgetExecutor.GrantConflict.selector);
        executor.execute(changed, changedOwnerSignature, changedExecution, changedExecutionSignature);
    }

    function test_samePurchaseCannotReplayAcrossGrants() public {
        AgentonomyBudgetExecutor.SpendGrant memory first = _grant(4, MAX_PER_PAYMENT, MAX_TOTAL);
        AgentonomyBudgetExecutor.PurchaseExecution memory firstExecution = _execution(first, 44, 40, 50);
        executor.execute(
            first, _sign(OWNER_KEY, executor.hashGrant(first)), firstExecution, _signExecution(firstExecution)
        );

        AgentonomyBudgetExecutor.SpendGrant memory second = _grant(5, MAX_PER_PAYMENT, MAX_TOTAL);
        AgentonomyBudgetExecutor.PurchaseExecution memory replay = _execution(second, 44, 41, 50);
        bytes memory replayOwnerSignature = _sign(OWNER_KEY, executor.hashGrant(second));
        bytes memory replayExecutionSignature = _signExecution(replay);
        vm.expectRevert(AgentonomyBudgetExecutor.PurchaseAlreadyPaid.selector);
        executor.execute(second, replayOwnerSignature, replay, replayExecutionSignature);
    }

    function test_revokeIsOwnerScopedAndWorksBeforeFirstUse() public {
        AgentonomyBudgetExecutor.SpendGrant memory grant = _grant(6, MAX_PER_PAYMENT, MAX_TOTAL);
        vm.prank(attacker);
        executor.revoke(grant.grantId);
        assertFalse(executor.revoked(owner, grant.grantId));

        vm.expectEmit(true, true, false, true);
        emit GrantRevoked(owner, grant.grantId);
        vm.prank(owner);
        executor.revoke(grant.grantId);
        assertTrue(executor.revoked(owner, grant.grantId));

        AgentonomyBudgetExecutor.PurchaseExecution memory execution = _execution(grant, 6, 60, 50);
        bytes memory ownerSignature = _sign(OWNER_KEY, executor.hashGrant(grant));
        bytes memory executionSignature = _signExecution(execution);
        vm.expectRevert(AgentonomyBudgetExecutor.GrantRevokedError.selector);
        executor.execute(grant, ownerSignature, execution, executionSignature);
    }

    function test_revokeAfterUseBlocksLaterPurchaseAndPreservesPriorPayment() public {
        AgentonomyBudgetExecutor.SpendGrant memory grant = _grant(23, MAX_PER_PAYMENT, MAX_TOTAL);
        AgentonomyBudgetExecutor.PurchaseExecution memory first = _execution(grant, 230, 230, 25);
        bytes memory ownerSignature = _sign(OWNER_KEY, executor.hashGrant(grant));
        executor.execute(grant, ownerSignature, first, _signExecution(first));
        uint256 spentBeforeRevoke = executor.spent(first.grantHash);
        uint256 payeeBeforeRevoke = token.balanceOf(payee);

        vm.prank(owner);
        executor.revoke(grant.grantId);

        AgentonomyBudgetExecutor.PurchaseExecution memory later = _execution(grant, 231, 231, 25);
        bytes memory laterExecutionSignature = _signExecution(later);
        vm.expectRevert(AgentonomyBudgetExecutor.GrantRevokedError.selector);
        executor.execute(grant, ownerSignature, later, laterExecutionSignature);
        assertEq(executor.spent(first.grantHash), spentBeforeRevoke);
        assertTrue(executor.paid(owner, first.purchaseId));
        assertFalse(executor.paid(owner, later.purchaseId));
        assertEq(token.balanceOf(payee), payeeBeforeRevoke);
    }

    function test_rejectsEveryInvalidGrantAndExecutionBoundary() public {
        AgentonomyBudgetExecutor.SpendGrant memory base = _grant(24, MAX_PER_PAYMENT, MAX_TOTAL);
        AgentonomyBudgetExecutor.PurchaseExecution memory execution = _execution(base, 240, 240, 25);

        base.owner = address(0);
        execution = _execution(base, 240, 240, 25);
        vm.expectRevert(AgentonomyBudgetExecutor.InvalidGrant.selector);
        executor.execute(base, new bytes(0), execution, new bytes(0));
        base = _grant(25, MAX_PER_PAYMENT, MAX_TOTAL);
        base.agentScope = bytes32(0);
        execution = _execution(base, 250, 250, 25);
        vm.expectRevert(AgentonomyBudgetExecutor.InvalidGrant.selector);
        executor.execute(base, new bytes(0), execution, new bytes(0));
        base = _grant(26, MAX_PER_PAYMENT, MAX_TOTAL);
        base.executionSigner = address(0);
        execution = _execution(base, 260, 260, 25);
        vm.expectRevert(AgentonomyBudgetExecutor.InvalidGrant.selector);
        executor.execute(base, new bytes(0), execution, new bytes(0));
        base = _grant(27, MAX_PER_PAYMENT, MAX_TOTAL);
        base.maxPerPayment = 0;
        execution = _execution(base, 270, 270, 25);
        vm.expectRevert(AgentonomyBudgetExecutor.InvalidGrant.selector);
        executor.execute(base, new bytes(0), execution, new bytes(0));
        base = _grant(28, MAX_PER_PAYMENT, MAX_TOTAL);
        base.maxTotal = 0;
        execution = _execution(base, 280, 280, 25);
        vm.expectRevert(AgentonomyBudgetExecutor.InvalidGrant.selector);
        executor.execute(base, new bytes(0), execution, new bytes(0));
        base = _grant(29, 2, 1);
        execution = _execution(base, 290, 290, 1);
        vm.expectRevert(AgentonomyBudgetExecutor.InvalidGrant.selector);
        executor.execute(base, new bytes(0), execution, new bytes(0));
        base = _grant(30, MAX_PER_PAYMENT, MAX_TOTAL);
        base.validAfter = base.validUntil;
        execution = _execution(base, 300, 300, 25);
        vm.expectRevert(AgentonomyBudgetExecutor.InvalidGrant.selector);
        executor.execute(base, new bytes(0), execution, new bytes(0));
        base = _grant(31, MAX_PER_PAYMENT, MAX_TOTAL);
        base.validAfter = base.validUntil + 1;
        execution = _execution(base, 310, 310, 25);
        vm.expectRevert(AgentonomyBudgetExecutor.InvalidGrant.selector);
        executor.execute(base, new bytes(0), execution, new bytes(0));

        base = _grant(32, MAX_PER_PAYMENT, MAX_TOTAL);
        execution = _execution(base, 320, 320, 25);
        execution.purchaseId = bytes32(0);
        vm.expectRevert(AgentonomyBudgetExecutor.InvalidExecution.selector);
        executor.execute(base, new bytes(0), execution, new bytes(0));
        execution = _execution(base, 321, 321, 25);
        execution.quoteHash = bytes32(0);
        vm.expectRevert(AgentonomyBudgetExecutor.InvalidExecution.selector);
        executor.execute(base, new bytes(0), execution, new bytes(0));
        execution = _execution(base, 322, 322, 0);
        vm.expectRevert(AgentonomyBudgetExecutor.InvalidExecution.selector);
        executor.execute(base, new bytes(0), execution, new bytes(0));
        execution = _execution(base, 323, 323, 25);
        execution.deadline = base.validUntil + 1;
        vm.expectRevert(AgentonomyBudgetExecutor.InvalidExecution.selector);
        executor.execute(base, new bytes(0), execution, new bytes(0));
    }

    function test_mutatingEachSignedGrantFieldInvalidatesOwnerSignature() public {
        AgentonomyBudgetExecutor.SpendGrant memory originalGrant = _grant(33, MAX_PER_PAYMENT, MAX_TOTAL);
        bytes memory originalOwnerSignature = _sign(OWNER_KEY, executor.hashGrant(originalGrant));

        AgentonomyBudgetExecutor.SpendGrant memory mutated = originalGrant;
        mutated.grantId = bytes32(uint256(34));
        _assertMutatedGrantRejected(mutated, originalOwnerSignature, 330);
        mutated = originalGrant;
        mutated.owner = attacker;
        _assertMutatedGrantRejected(mutated, originalOwnerSignature, 331);
        mutated = originalGrant;
        mutated.agentScope = bytes32(uint256(1033));
        _assertMutatedGrantRejected(mutated, originalOwnerSignature, 332);
        mutated = originalGrant;
        mutated.payee = address(0xCAFE);
        _assertMutatedGrantRejected(mutated, originalOwnerSignature, 333);
        mutated = originalGrant;
        mutated.maxPerPayment = MAX_PER_PAYMENT - 1;
        _assertMutatedGrantRejected(mutated, originalOwnerSignature, 334);
        mutated = originalGrant;
        mutated.maxTotal = MAX_TOTAL - 1;
        _assertMutatedGrantRejected(mutated, originalOwnerSignature, 335);
        mutated = originalGrant;
        mutated.validAfter = VALID_AFTER - 1;
        _assertMutatedGrantRejected(mutated, originalOwnerSignature, 336);
        mutated = originalGrant;
        mutated.validUntil = VALID_UNTIL - 1;
        _assertMutatedGrantRejected(mutated, originalOwnerSignature, 337);
        mutated = originalGrant;
        mutated.executionSigner = attacker;
        _assertMutatedGrantRejected(mutated, originalOwnerSignature, 338);

        mutated = originalGrant;
        mutated.token = address(0xCAFE);
        AgentonomyBudgetExecutor.PurchaseExecution memory mutatedTokenExecution = _execution(mutated, 339, 339, 25);
        vm.expectRevert(AgentonomyBudgetExecutor.InvalidGrant.selector);
        executor.execute(mutated, originalOwnerSignature, mutatedTokenExecution, new bytes(0));
    }

    function test_rejectsWrongChainAndInvalidStaticGrantFields() public {
        AgentonomyBudgetExecutor.SpendGrant memory grant = _grant(7, MAX_PER_PAYMENT, MAX_TOTAL);
        AgentonomyBudgetExecutor.PurchaseExecution memory execution = _execution(grant, 7, 70, 50);

        vm.chainId(DEPLOYMENT_CHAIN_ID + 1);
        bytes memory ownerSignature = _sign(OWNER_KEY, executor.hashGrant(grant));
        bytes memory executionSignature = _signExecution(execution);
        vm.expectRevert(AgentonomyBudgetExecutor.WrongChain.selector);
        executor.execute(grant, ownerSignature, execution, executionSignature);
        vm.chainId(DEPLOYMENT_CHAIN_ID);

        grant.grantId = bytes32(0);
        vm.expectRevert(AgentonomyBudgetExecutor.InvalidGrant.selector);
        executor.execute(grant, new bytes(0), execution, new bytes(0));
    }

    function test_rejectsMutatedSignerAndMalformedSignatures() public {
        AgentonomyBudgetExecutor.SpendGrant memory grant = _grant(8, MAX_PER_PAYMENT, MAX_TOTAL);
        AgentonomyBudgetExecutor.PurchaseExecution memory execution = _execution(grant, 8, 80, 50);
        bytes memory ownerSignature = _sign(OWNER_KEY, executor.hashGrant(grant));
        bytes memory executionSignature = _signExecution(execution);

        grant.payee = address(0xCAFE);
        execution.grantHash = executor.hashGrant(grant);
        vm.expectRevert(AgentonomyBudgetExecutor.InvalidSignature.selector);
        executor.execute(grant, ownerSignature, execution, executionSignature);

        grant = _grant(9, MAX_PER_PAYMENT, MAX_TOTAL);
        execution = _execution(grant, 9, 90, 50);
        bytes memory validOwnerSignature = _sign(OWNER_KEY, executor.hashGrant(grant));
        vm.expectRevert(AgentonomyBudgetExecutor.InvalidSignature.selector);
        executor.execute(grant, new bytes(64), execution, executionSignature);

        bytes memory malformedExecutionSignature = new bytes(64);
        vm.expectRevert(AgentonomyBudgetExecutor.InvalidSignature.selector);
        executor.execute(grant, validOwnerSignature, execution, malformedExecutionSignature);
    }

    function test_rejectsWrongExecutionSignerAndHighS() public {
        AgentonomyBudgetExecutor.SpendGrant memory grant = _grant(19, MAX_PER_PAYMENT, MAX_TOTAL);
        AgentonomyBudgetExecutor.PurchaseExecution memory execution = _execution(grant, 19, 190, 50);
        bytes memory ownerSignature = _sign(OWNER_KEY, executor.hashGrant(grant));
        bytes memory wrongExecutionSignature = _sign(ATTACKER_KEY, executor.hashExecution(execution));
        vm.expectRevert(AgentonomyBudgetExecutor.InvalidSignature.selector);
        executor.execute(grant, ownerSignature, execution, wrongExecutionSignature);

        bytes memory highSSignature = _signExecution(execution);
        bytes32 r;
        bytes32 s;
        uint8 v;
        assembly {
            r := mload(add(highSSignature, 32))
            s := mload(add(highSSignature, 64))
            v := byte(0, mload(add(highSSignature, 96)))
        }
        if (uint256(s) <= SECP256K1_HALF_N) {
            s = bytes32(SECP256K1_N - uint256(s));
            v = v == 27 ? 28 : 27;
        }
        highSSignature = abi.encodePacked(r, s, v);
        vm.expectRevert(AgentonomyBudgetExecutor.InvalidSignature.selector);
        executor.execute(grant, ownerSignature, execution, highSSignature);
    }

    function test_rejectsGrantAndExecutionWindowsAtBoundaries() public {
        AgentonomyBudgetExecutor.SpendGrant memory grant = _grant(10, MAX_PER_PAYMENT, MAX_TOTAL);
        AgentonomyBudgetExecutor.PurchaseExecution memory execution = _execution(grant, 10, 100, 50);

        vm.warp(grant.validUntil);
        bytes memory ownerSignature = _sign(OWNER_KEY, executor.hashGrant(grant));
        bytes memory executionSignature = _signExecution(execution);
        vm.expectRevert(AgentonomyBudgetExecutor.GrantExpired.selector);
        executor.execute(grant, ownerSignature, execution, executionSignature);

        vm.warp(grant.validAfter - 1);
        vm.expectRevert(AgentonomyBudgetExecutor.GrantNotActive.selector);
        executor.execute(grant, ownerSignature, execution, executionSignature);

        vm.warp(VALID_AFTER);
        execution.deadline = block.timestamp;
        execution.grantHash = executor.hashGrant(grant);
        executionSignature = _signExecution(execution);
        vm.expectRevert(AgentonomyBudgetExecutor.ExecutionExpired.selector);
        executor.execute(grant, ownerSignature, execution, executionSignature);
    }

    function test_rejectsPerPaymentAndTotalExceededWithoutStateChange() public {
        AgentonomyBudgetExecutor.SpendGrant memory grant = _grant(11, 50, 100);
        AgentonomyBudgetExecutor.PurchaseExecution memory tooLarge = _execution(grant, 11, 110, 51);
        bytes memory grantOwnerSignature = _sign(OWNER_KEY, executor.hashGrant(grant));
        bytes memory tooLargeExecutionSignature = _signExecution(tooLarge);
        vm.expectRevert(AgentonomyBudgetExecutor.PerPaymentExceeded.selector);
        executor.execute(grant, grantOwnerSignature, tooLarge, tooLargeExecutionSignature);
        assertEq(executor.spent(executor.hashGrant(grant)), 0);

        AgentonomyBudgetExecutor.PurchaseExecution memory first = _execution(grant, 12, 111, 50);
        executor.execute(grant, _sign(OWNER_KEY, executor.hashGrant(grant)), first, _signExecution(first));
        AgentonomyBudgetExecutor.PurchaseExecution memory second = _execution(grant, 13, 112, 51);
        bytes memory secondExecutionSignature = _signExecution(second);
        vm.expectRevert(AgentonomyBudgetExecutor.PerPaymentExceeded.selector);
        executor.execute(grant, grantOwnerSignature, second, secondExecutionSignature);

        AgentonomyBudgetExecutor.SpendGrant memory totalGrant = _grant(12, 100, 100);
        AgentonomyBudgetExecutor.PurchaseExecution memory totalFirst = _execution(totalGrant, 14, 113, 100);
        executor.execute(
            totalGrant, _sign(OWNER_KEY, executor.hashGrant(totalGrant)), totalFirst, _signExecution(totalFirst)
        );
        AgentonomyBudgetExecutor.PurchaseExecution memory totalSecond = _execution(totalGrant, 15, 114, 1);
        bytes memory totalGrantOwnerSignature = _sign(OWNER_KEY, executor.hashGrant(totalGrant));
        bytes memory totalSecondExecutionSignature = _signExecution(totalSecond);
        vm.expectRevert(AgentonomyBudgetExecutor.TotalExceeded.selector);
        executor.execute(totalGrant, totalGrantOwnerSignature, totalSecond, totalSecondExecutionSignature);
        assertEq(executor.spent(executor.hashGrant(totalGrant)), 100);
    }

    function test_rejectsWrongTokenPayeeAndExecutionBinding() public {
        AgentonomyBudgetExecutor.SpendGrant memory grant = _grant(13, MAX_PER_PAYMENT, MAX_TOTAL);
        AgentonomyBudgetExecutor.PurchaseExecution memory execution = _execution(grant, 16, 130, 50);

        grant.token = address(0xCAFE);
        vm.expectRevert(AgentonomyBudgetExecutor.InvalidGrant.selector);
        executor.execute(grant, new bytes(0), execution, new bytes(0));

        grant = _grant(14, MAX_PER_PAYMENT, MAX_TOTAL);
        grant.payee = grant.owner;
        execution = _execution(grant, 17, 131, 50);
        vm.expectRevert(AgentonomyBudgetExecutor.InvalidGrant.selector);
        executor.execute(grant, new bytes(0), execution, new bytes(0));

        grant = _grant(15, MAX_PER_PAYMENT, MAX_TOTAL);
        execution = _execution(grant, 18, 132, 50);
        execution.grantHash = bytes32(uint256(1));
        vm.expectRevert(AgentonomyBudgetExecutor.InvalidExecution.selector);
        executor.execute(grant, new bytes(0), execution, new bytes(0));
    }

    function test_adversarialTokenModesRevertAtomically() public {
        BudgetTestUSD.Mode[7] memory modes = [
            BudgetTestUSD.Mode.False,
            BudgetTestUSD.Mode.Malformed,
            BudgetTestUSD.Mode.Reverting,
            BudgetTestUSD.Mode.FeeOnTransfer,
            BudgetTestUSD.Mode.NoOwnerDebit,
            BudgetTestUSD.Mode.NoPayeeCredit,
            BudgetTestUSD.Mode.MalformedBalance
        ];
        for (uint256 index; index < modes.length; index++) {
            AgentonomyBudgetExecutor.SpendGrant memory grant = _grant(16 + index, MAX_PER_PAYMENT, MAX_TOTAL);
            AgentonomyBudgetExecutor.PurchaseExecution memory execution = _execution(grant, 20 + index, 160 + index, 25);
            bytes memory ownerSignature = _sign(OWNER_KEY, executor.hashGrant(grant));
            bytes memory executionSignature = _signExecution(execution);
            uint256 ownerBefore = token.balanceOf(owner);
            uint256 payeeBefore = token.balanceOf(payee);
            uint256 allowanceBefore = token.allowance(owner, address(executor));
            token.configure(address(executor), modes[index]);
            vm.expectRevert(AgentonomyBudgetExecutor.TransferFailed.selector);
            executor.execute(grant, ownerSignature, execution, executionSignature);
            token.configure(address(executor), BudgetTestUSD.Mode.Success);
            assertEq(executor.spent(executor.hashGrant(grant)), 0);
            assertFalse(executor.paid(owner, execution.purchaseId));
            assertEq(token.balanceOf(owner), ownerBefore);
            assertEq(token.balanceOf(payee), payeeBefore);
            assertEq(token.allowance(owner, address(executor)), allowanceBefore);

            executor.execute(grant, ownerSignature, execution, executionSignature);
            assertEq(executor.spent(execution.grantHash), execution.amount);
            assertTrue(executor.paid(owner, execution.purchaseId));
        }
    }

    function test_insufficientAllowanceAndBalanceRevertAtomically() public {
        AgentonomyBudgetExecutor.SpendGrant memory grant = _grant(20, MAX_PER_PAYMENT, MAX_TOTAL);
        AgentonomyBudgetExecutor.PurchaseExecution memory allowanceExecution = _execution(grant, 200, 200, 25);
        bytes memory ownerSignature = _sign(OWNER_KEY, executor.hashGrant(grant));
        bytes memory allowanceExecutionSignature = _signExecution(allowanceExecution);
        vm.prank(owner);
        token.approve(address(executor), 0);
        uint256 ownerBefore = token.balanceOf(owner);
        uint256 payeeBefore = token.balanceOf(payee);
        uint256 allowanceBefore = token.allowance(owner, address(executor));
        vm.expectRevert(AgentonomyBudgetExecutor.TransferFailed.selector);
        executor.execute(grant, ownerSignature, allowanceExecution, allowanceExecutionSignature);
        assertEq(executor.spent(allowanceExecution.grantHash), 0);
        assertFalse(executor.paid(owner, allowanceExecution.purchaseId));
        assertEq(token.balanceOf(owner), ownerBefore);
        assertEq(token.balanceOf(payee), payeeBefore);
        assertEq(token.allowance(owner, address(executor)), allowanceBefore);

        vm.prank(owner);
        token.approve(address(executor), type(uint256).max);
        token.configure(address(executor), BudgetTestUSD.Mode.Success);
        executor.execute(grant, ownerSignature, allowanceExecution, allowanceExecutionSignature);
        assertEq(executor.spent(allowanceExecution.grantHash), allowanceExecution.amount);
        assertTrue(executor.paid(owner, allowanceExecution.purchaseId));

        token.burn(owner, token.balanceOf(owner));
        AgentonomyBudgetExecutor.PurchaseExecution memory balanceExecution = _execution(grant, 201, 201, 25);
        bytes memory balanceExecutionSignature = _signExecution(balanceExecution);
        ownerBefore = token.balanceOf(owner);
        payeeBefore = token.balanceOf(payee);
        allowanceBefore = token.allowance(owner, address(executor));
        vm.expectRevert(AgentonomyBudgetExecutor.TransferFailed.selector);
        executor.execute(grant, ownerSignature, balanceExecution, balanceExecutionSignature);
        assertEq(executor.spent(balanceExecution.grantHash), allowanceExecution.amount);
        assertFalse(executor.paid(owner, balanceExecution.purchaseId));
        assertEq(token.balanceOf(owner), ownerBefore);
        assertEq(token.balanceOf(payee), payeeBefore);
        assertEq(token.allowance(owner, address(executor)), allowanceBefore);

        token.mint(owner, balanceExecution.amount);
        executor.execute(grant, ownerSignature, balanceExecution, balanceExecutionSignature);
        assertEq(executor.spent(balanceExecution.grantHash), allowanceExecution.amount + balanceExecution.amount);
        assertTrue(executor.paid(owner, balanceExecution.purchaseId));
    }

    function test_reentrancyIsRejectedWhileOuterPaymentRemainsAtomic() public {
        AgentonomyBudgetExecutor.SpendGrant memory grant = _grant(17, MAX_PER_PAYMENT, MAX_TOTAL);
        AgentonomyBudgetExecutor.PurchaseExecution memory execution = _execution(grant, 17, 170, 25);
        bytes memory ownerSignature = _sign(OWNER_KEY, executor.hashGrant(grant));
        bytes memory executionSignature = _signExecution(execution);
        token.configure(address(executor), BudgetTestUSD.Mode.Reentrant);
        token.setReentry(
            abi.encodeWithSelector(
                AgentonomyBudgetExecutor.execute.selector, grant, ownerSignature, execution, executionSignature
            )
        );

        executor.execute(grant, ownerSignature, execution, executionSignature);

        assertTrue(token.reentryAttempted());
        assertFalse(token.reentrySucceeded());
        assertEq(executor.spent(execution.grantHash), execution.amount);
        assertTrue(executor.paid(owner, execution.purchaseId));
    }

    function test_effectsAreVisibleToTokenBeforeTransfer() public {
        AgentonomyBudgetExecutor.SpendGrant memory grant = _grant(21, MAX_PER_PAYMENT, MAX_TOTAL);
        AgentonomyBudgetExecutor.PurchaseExecution memory execution = _execution(grant, 210, 210, 25);
        bytes memory ownerSignature = _sign(OWNER_KEY, executor.hashGrant(grant));
        bytes memory executionSignature = _signExecution(execution);
        token.configureObservation(execution.grantHash, owner, grant.grantId, execution.purchaseId);
        token.configure(address(executor), BudgetTestUSD.Mode.ObserveEffects);

        executor.execute(grant, ownerSignature, execution, executionSignature);

        assertTrue(token.effectsObserved());
    }

    function testFuzz_successfulSpendingNeverExceedsGrantTotal(uint96 firstRaw, uint96 secondRaw) public {
        uint256 first = uint256(firstRaw) % 101;
        uint256 second = uint256(secondRaw) % 101;
        vm.assume(first > 0);
        vm.assume(second > 0);
        AgentonomyBudgetExecutor.SpendGrant memory grant = _grant(18, 100, 100);
        AgentonomyBudgetExecutor.PurchaseExecution memory firstExecution = _execution(grant, 180, 180, first);
        executor.execute(
            grant, _sign(OWNER_KEY, executor.hashGrant(grant)), firstExecution, _signExecution(firstExecution)
        );

        AgentonomyBudgetExecutor.PurchaseExecution memory secondExecution = _execution(grant, 181, 181, second);
        if (second <= 100 - first) {
            executor.execute(
                grant, _sign(OWNER_KEY, executor.hashGrant(grant)), secondExecution, _signExecution(secondExecution)
            );
        } else {
            bytes memory ownerSignature = _sign(OWNER_KEY, executor.hashGrant(grant));
            bytes memory executionSignature = _signExecution(secondExecution);
            vm.expectRevert(AgentonomyBudgetExecutor.TotalExceeded.selector);
            executor.execute(grant, ownerSignature, secondExecution, executionSignature);
        }
        assertTrue(executor.spent(executor.hashGrant(grant)) <= grant.maxTotal);
    }

    function test_constructorRejectsTokenWithoutCode() public {
        vm.expectRevert(AgentonomyBudgetExecutor.InvalidGrant.selector);
        new AgentonomyBudgetExecutor(address(0x1234));
    }

    function _grant(uint256 grantId, uint256 maxPerPayment, uint256 maxTotal)
        internal
        view
        returns (AgentonomyBudgetExecutor.SpendGrant memory grant)
    {
        grant = AgentonomyBudgetExecutor.SpendGrant({
            grantId: bytes32(grantId),
            owner: owner,
            agentScope: bytes32(uint256(grantId + 1_000)),
            token: address(token),
            payee: payee,
            maxPerPayment: maxPerPayment,
            maxTotal: maxTotal,
            validAfter: VALID_AFTER,
            validUntil: VALID_UNTIL,
            executionSigner: signer
        });
    }

    function _execution(
        AgentonomyBudgetExecutor.SpendGrant memory grant,
        uint256 purchaseId,
        uint256 quoteHash,
        uint256 amount
    ) internal view returns (AgentonomyBudgetExecutor.PurchaseExecution memory execution) {
        execution = AgentonomyBudgetExecutor.PurchaseExecution({
                grantHash: executor.hashGrant(grant),
                purchaseId: bytes32(purchaseId),
                quoteHash: bytes32(quoteHash),
                amount: amount,
                deadline: EXECUTION_DEADLINE
            });
    }

    function _sign(uint256 privateKey, bytes32 digest) internal returns (bytes memory signature) {
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(privateKey, digest);
        signature = abi.encodePacked(r, s, v);
    }

    function _signDigest(uint256 privateKey, bytes32 digest) internal returns (bytes memory signature) {
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(privateKey, digest);
        signature = abi.encodePacked(r, s, v);
    }

    function _signExecution(AgentonomyBudgetExecutor.PurchaseExecution memory execution)
        internal
        returns (bytes memory)
    {
        return _sign(EXECUTION_SIGNER_KEY, executor.hashExecution(execution));
    }

    function _assertMutatedGrantRejected(
        AgentonomyBudgetExecutor.SpendGrant memory grant,
        bytes memory originalOwnerSignature,
        uint256 purchaseId
    ) internal {
        AgentonomyBudgetExecutor.PurchaseExecution memory execution = _execution(grant, purchaseId, purchaseId, 25);
        bytes memory executionSignature = _signExecution(execution);
        vm.expectRevert(AgentonomyBudgetExecutor.InvalidSignature.selector);
        executor.execute(grant, originalOwnerSignature, execution, executionSignature);
    }

    function _literalDomain(uint256 chainId, address verifyingContract) internal pure returns (bytes32) {
        return keccak256(
            abi.encode(
                VECTOR_DOMAIN_TYPEHASH,
                keccak256("Agentonomy Budget Executor"),
                keccak256("1"),
                chainId,
                verifyingContract
            )
        );
    }

    function _literalGrantDigest(
        AgentonomyBudgetExecutor.SpendGrant memory grant,
        uint256 chainId,
        address verifyingContract
    ) internal pure returns (bytes32) {
        bytes32 structHash = keccak256(
            abi.encode(
                VECTOR_GRANT_TYPEHASH,
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
        return keccak256(abi.encodePacked("\x19\x01", _literalDomain(chainId, verifyingContract), structHash));
    }

    function _literalExecutionDigest(
        AgentonomyBudgetExecutor.PurchaseExecution memory execution,
        uint256 chainId,
        address verifyingContract
    ) internal pure returns (bytes32) {
        bytes32 structHash = keccak256(
            abi.encode(
                VECTOR_EXECUTION_TYPEHASH,
                execution.grantHash,
                execution.purchaseId,
                execution.quoteHash,
                execution.amount,
                execution.deadline
            )
        );
        return keccak256(abi.encodePacked("\x19\x01", _literalDomain(chainId, verifyingContract), structHash));
    }

    function _assertPaymentEvent(
        BudgetVm.Log[] memory logs,
        AgentonomyBudgetExecutor.PurchaseExecution memory execution,
        AgentonomyBudgetExecutor.SpendGrant memory grant
    ) internal view {
        bytes32 expectedTopic = keccak256(
            "PaymentExecuted(bytes32,bytes32,address,bytes32,bytes32,address,address,uint256)"
        );
        bool found;
        for (uint256 index; index < logs.length; index++) {
            if (
                logs[index].emitter == address(executor) && logs[index].topics.length == 4
                    && logs[index].topics[0] == expectedTopic
            ) {
                found = true;
                assertEq(logs[index].topics[1], execution.grantHash);
                assertEq(logs[index].topics[2], execution.purchaseId);
                assertEq(address(uint160(uint256(logs[index].topics[3]))), grant.owner);
                (bytes32 eventGrantId, bytes32 eventQuoteHash, address eventToken, address eventPayee, uint256 amount) =
                    abi.decode(logs[index].data, (bytes32, bytes32, address, address, uint256));
                assertEq(eventGrantId, grant.grantId);
                assertEq(eventQuoteHash, execution.quoteHash);
                assertEq(eventToken, grant.token);
                assertEq(eventPayee, grant.payee);
                assertEq(amount, execution.amount);
            }
        }
        assertTrue(found);
    }

    function assertTrue(bool condition) internal pure {
        require(condition, "assertion failed");
    }

    function assertFalse(bool condition) internal pure {
        require(!condition, "assertion failed");
    }

    function assertEq(uint256 left, uint256 right) internal pure {
        require(left == right, "uint256 assertion failed");
    }

    function assertEq(address left, address right) internal pure {
        require(left == right, "address assertion failed");
    }

    function assertEq(bytes32 left, bytes32 right) internal pure {
        require(left == right, "bytes32 assertion failed");
    }
}
