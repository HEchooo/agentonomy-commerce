// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.24;

import {AgentonomyFaucetUSD} from "../src/AgentonomyFaucetUSD.sol";

interface FaucetVm {
    function addr(uint256 privateKey) external returns (address);
    function chainId(uint256 newChainId) external;
    function expectRevert() external;
    function expectRevert(bytes4 selector) external;
    function expectRevert(bytes calldata revertData) external;
    function prank(address sender) external;
}

contract AgentonomyFaucetUSDTest {
    FaucetVm internal constant vm = FaucetVm(address(uint160(uint256(keccak256("hevm cheat code")))));

    uint256 internal constant MONAD_TESTNET_CHAIN_ID = 10_143;
    uint256 internal constant LOCAL_ANVIL_CHAIN_ID = 31_337;
    uint256 internal constant CLAIM_AMOUNT = 1_000_000;
    uint256 internal constant TOTAL_SUPPLY = 1_000 * CLAIM_AMOUNT;

    AgentonomyFaucetUSD internal token;
    address internal alice;
    address internal bob;
    address internal carol;
    address internal attacker;

    function setUp() public {
        vm.chainId(LOCAL_ANVIL_CHAIN_ID);
        token = new AgentonomyFaucetUSD();
        alice = vm.addr(0xA11CE);
        bob = vm.addr(0xB0B);
        carol = vm.addr(0xC0DE);
        attacker = vm.addr(0xBAD);
    }

    function test_constructorSeedsPoolAndExposesFixedTokenMetadata() public view {
        assertEq(token.name(), "Agentonomy Test USD Faucet - No Value");
        assertEq(token.symbol(), "TestUSD");
        assertEq(token.decimals(), 6);
        assertEq(token.totalSupply(), TOTAL_SUPPLY);
        assertEq(token.balanceOf(address(token)), TOTAL_SUPPLY);
        assertEq(token.balanceOf(alice), 0);
        assertFalse(token.claimed(alice));
    }

    function test_eachCallerClaimsOnceToSelfAndDifferentCallersAreIndependent() public {
        vm.prank(alice);
        token.claim();

        assertTrue(token.claimed(alice));
        assertEq(token.balanceOf(alice), CLAIM_AMOUNT);
        assertEq(token.balanceOf(address(token)), TOTAL_SUPPLY - CLAIM_AMOUNT);

        vm.prank(alice);
        vm.expectRevert(AgentonomyFaucetUSD.AlreadyClaimed.selector);
        token.claim();

        vm.prank(bob);
        token.claim();
        assertTrue(token.claimed(bob));
        assertEq(token.balanceOf(bob), CLAIM_AMOUNT);
        assertEq(token.balanceOf(alice), CLAIM_AMOUNT);
        assertEq(token.balanceOf(address(token)), TOTAL_SUPPLY - 2 * CLAIM_AMOUNT);
    }

    function test_claimPoolExhaustionRevertsWithoutIssuingMore() public {
        for (uint256 index = 1; index <= TOTAL_SUPPLY / CLAIM_AMOUNT; index++) {
            vm.prank(vm.addr(index));
            token.claim();
        }

        address nextCaller = vm.addr(TOTAL_SUPPLY / CLAIM_AMOUNT + 1);
        assertEq(token.balanceOf(address(token)), 0);
        vm.prank(nextCaller);
        vm.expectRevert(AgentonomyFaucetUSD.InsufficientPool.selector);
        token.claim();
        assertFalse(token.claimed(nextCaller));
        assertEq(token.balanceOf(nextCaller), 0);
        assertEq(token.totalSupply(), TOTAL_SUPPLY);
    }

    function test_allowanceTransferFromMovesExactAmounts() public {
        uint256 transferAmount = 400_000;
        vm.prank(alice);
        token.claim();

        vm.prank(alice);
        token.approve(bob, transferAmount);
        assertEq(token.allowance(alice, bob), transferAmount);

        vm.prank(bob);
        token.transferFrom(alice, carol, transferAmount);

        assertEq(token.balanceOf(alice), CLAIM_AMOUNT - transferAmount);
        assertEq(token.balanceOf(carol), transferAmount);
        assertEq(token.allowance(alice, bob), 0);
    }

    function test_poolCannotBeDrainedThroughUnauthorizedTransferFrom() public {
        uint256 poolBefore = token.balanceOf(address(token));
        vm.prank(attacker);
        vm.expectRevert(AgentonomyFaucetUSD.InsufficientAllowance.selector);
        token.transferFrom(address(token), attacker, CLAIM_AMOUNT);

        assertEq(token.balanceOf(address(token)), poolBefore);
        assertEq(token.balanceOf(attacker), 0);
        assertFalse(token.claimed(attacker));
    }

    function test_totalSupplyRemainsConservedAcrossClaimsAndTransfers() public {
        vm.prank(alice);
        token.claim();
        vm.prank(bob);
        token.claim();
        vm.prank(alice);
        token.transfer(carol, 250_000);

        uint256 balances =
            token.balanceOf(address(token)) + token.balanceOf(alice) + token.balanceOf(bob) + token.balanceOf(carol);
        assertEq(balances, token.totalSupply());
    }

    function test_zeroAddressesAndZeroTransfersRevert() public {
        vm.prank(alice);
        vm.expectRevert(AgentonomyFaucetUSD.ZeroAddress.selector);
        token.approve(address(0), CLAIM_AMOUNT);

        vm.prank(alice);
        token.claim();

        vm.prank(alice);
        vm.expectRevert(AgentonomyFaucetUSD.ZeroAddress.selector);
        token.transfer(address(0), CLAIM_AMOUNT);

        vm.prank(alice);
        vm.expectRevert(AgentonomyFaucetUSD.ZeroAmount.selector);
        token.transfer(carol, 0);

        vm.prank(alice);
        token.approve(bob, CLAIM_AMOUNT);
        vm.prank(bob);
        vm.expectRevert(AgentonomyFaucetUSD.ZeroAddress.selector);
        token.transferFrom(alice, address(0), CLAIM_AMOUNT);

        vm.prank(bob);
        vm.expectRevert(AgentonomyFaucetUSD.ZeroAmount.selector);
        token.transferFrom(alice, carol, 0);
    }

    function test_unknownMintSelectorCannotExpandSupply() public {
        (bool success,) = address(token).call(abi.encodeWithSignature("mint(address,uint256)", alice, CLAIM_AMOUNT));
        assertFalse(success);
        assertEq(token.totalSupply(), TOTAL_SUPPLY);
        assertEq(token.balanceOf(alice), 0);
    }

    function test_constructorAcceptsMonadTestnet() public {
        vm.chainId(MONAD_TESTNET_CHAIN_ID);
        AgentonomyFaucetUSD testnetToken = new AgentonomyFaucetUSD();
        assertEq(testnetToken.totalSupply(), TOTAL_SUPPLY);
        assertEq(testnetToken.balanceOf(address(testnetToken)), TOTAL_SUPPLY);
    }

    function test_constructorRejectsUnsupportedChain() public {
        vm.chainId(1);
        vm.expectRevert(abi.encodeWithSelector(AgentonomyFaucetUSD.UnsupportedChain.selector, uint256(1)));
        new AgentonomyFaucetUSD();
    }

    function assertEq(uint256 left, uint256 right) internal pure {
        if (left != right) revert("assertion failed");
    }

    function assertEq(address left, address right) internal pure {
        if (left != right) revert("assertion failed");
    }

    function assertEq(string memory left, string memory right) internal pure {
        if (keccak256(bytes(left)) != keccak256(bytes(right))) revert("assertion failed");
    }

    function assertTrue(bool condition) internal pure {
        if (!condition) revert("assertion failed");
    }

    function assertFalse(bool condition) internal pure {
        if (condition) revert("assertion failed");
    }
}
