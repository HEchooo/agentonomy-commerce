// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.24;

interface BudgetDeployTestVm {
    function chainId(uint256 newChainId) external;
    function expectRevert(bytes4 selector) external;
    function setEnv(string calldata key, string calldata value) external;
    function toString(address value) external pure returns (string memory);
    function toString(uint256 value) external pure returns (string memory);
    function toString(bytes32 value) external pure returns (string memory);
}

import {DeployAgentonomyBudgetExecutor} from "../script/DeployAgentonomyBudgetExecutor.s.sol";
import {AgentonomyBudgetExecutor} from "../src/AgentonomyBudgetExecutor.sol";
import {AgentonomyTestUSD} from "../src/AgentonomyTestUSD.sol";

contract DeployAgentonomyBudgetExecutorHarness is DeployAgentonomyBudgetExecutor {
    function validate(address token, uint256 configuredChainId) external view {
        _validateConfig(token, configuredChainId, token.codehash);
    }

    function validateWithCodeHash(address token, uint256 configuredChainId, bytes32 expectedCodeHash) external view {
        _validateConfig(token, configuredChainId, expectedCodeHash);
    }

    function deployChecked(address token, uint256 chain, bytes32 tokenHash, bytes32 initHash)
        external
        returns (AgentonomyBudgetExecutor)
    {
        return _deploy(token, chain, tokenHash, initHash, false);
    }
}

contract DeployAgentonomyBudgetExecutorTest {
    BudgetDeployTestVm internal constant vm =
        BudgetDeployTestVm(address(uint160(uint256(keccak256("hevm cheat code")))));
    uint256 internal constant CHAIN_ID = 31_337;
    DeployAgentonomyBudgetExecutorHarness internal harness;
    AgentonomyTestUSD internal token;

    function setUp() public {
        vm.chainId(CHAIN_ID);
        harness = new DeployAgentonomyBudgetExecutorHarness();
        token = new AgentonomyTestUSD(address(this), 1_000_000);
    }

    function test_validationRequiresExplicitDeployedTokenAndCurrentChain() public {
        harness.validate(address(token), CHAIN_ID);

        vm.expectRevert(DeployAgentonomyBudgetExecutor.MissingToken.selector);
        harness.validate(address(0), CHAIN_ID);

        vm.expectRevert(DeployAgentonomyBudgetExecutor.MissingCodeHash.selector);
        harness.validate(address(0x1234), CHAIN_ID);

        vm.expectRevert(DeployAgentonomyBudgetExecutor.InvalidToken.selector);
        harness.validateWithCodeHash(address(0x1234), CHAIN_ID, bytes32(uint256(1)));

        vm.expectRevert(DeployAgentonomyBudgetExecutor.WrongChain.selector);
        harness.validate(address(token), CHAIN_ID + 1);

        vm.expectRevert(DeployAgentonomyBudgetExecutor.MissingCodeHash.selector);
        harness.validateWithCodeHash(address(token), CHAIN_ID, bytes32(0));

        vm.expectRevert(DeployAgentonomyBudgetExecutor.InvalidToken.selector);
        harness.validateWithCodeHash(address(token), CHAIN_ID, bytes32(uint256(1)));
    }

    function test_validationRequiresTestUsdIdentityAndConfiguredTokenCodeHash() public {
        AgentonomyTestUSD another = new AgentonomyTestUSD(address(this), 1_000_000);
        harness.validateWithCodeHash(address(another), CHAIN_ID, address(another).codehash);

        AgentonomyBudgetExecutor deployed = new AgentonomyBudgetExecutor(address(token));
        assertEq(deployed.TOKEN(), address(token));
        assertEq(deployed.EXECUTION_CHAIN_ID(), CHAIN_ID);
    }

    function test_runDryRunUsesExplicitTokenChainAndAssetHash() public {
        AgentonomyBudgetExecutor deployed = harness.deployChecked(
            address(token), CHAIN_ID, address(token).codehash, harness.initCodeHash(address(token))
        );
        assertEq(deployed.TOKEN(), address(token));
        assertEq(deployed.EXECUTION_CHAIN_ID(), CHAIN_ID);
    }

    function test_runRejectsUnexpectedExecutorCodeHash() public {
        vm.expectRevert(DeployAgentonomyBudgetExecutor.InvalidToken.selector);
        harness.deployChecked(address(token), CHAIN_ID, address(token).codehash, bytes32(uint256(1)));
        vm.expectRevert(DeployAgentonomyBudgetExecutor.MissingCodeHash.selector);
        harness.deployChecked(address(token), CHAIN_ID, address(token).codehash, bytes32(0));
    }

    function assertEq(address left, address right) internal pure {
        require(left == right, "address assertion failed");
    }

    function assertEq(uint256 left, uint256 right) internal pure {
        require(left == right, "uint256 assertion failed");
    }
}
