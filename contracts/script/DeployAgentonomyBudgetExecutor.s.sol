// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.24;

import {AgentonomyBudgetExecutor} from "../src/AgentonomyBudgetExecutor.sol";

interface BudgetDeployVm {
    function envAddress(string calldata key) external returns (address);
    function envOr(string calldata key, bool defaultValue) external returns (bool);
    function envOr(string calldata key, bytes32 defaultValue) external returns (bytes32);
    function envBytes32(string calldata key) external returns (bytes32);
    function envUint(string calldata key) external returns (uint256);
    function startBroadcast() external;
    function stopBroadcast() external;
}

/// @notice Deploys the budget executor only for an explicitly configured token and chain.
/// @dev Dry-run is the default. Set CLINK_BUDGET_BROADCAST=true explicitly to mark the
///      deployment transaction for Foundry broadcast; no private key is read by this script.
contract DeployAgentonomyBudgetExecutor {
    BudgetDeployVm internal constant vm = BudgetDeployVm(address(uint160(uint256(keccak256("hevm cheat code")))));

    error WrongChain();
    error MissingToken();
    error MissingCodeHash();
    error InvalidToken();

    event BudgetExecutorDeployed(address indexed executor, address indexed token, uint256 indexed chainId);

    function run() external returns (AgentonomyBudgetExecutor deployed) {
        address token = vm.envAddress("CLINK_BUDGET_TOKEN");
        uint256 configuredChainId = vm.envUint("CLINK_BUDGET_CHAIN_ID");
        bytes32 expectedTokenCodeHash = vm.envBytes32("CLINK_BUDGET_EXPECTED_TOKEN_CODEHASH");
        bytes32 expectedInitCodeHash = vm.envBytes32("CLINK_BUDGET_EXPECTED_INIT_CODEHASH");
        return _deploy(
            token,
            configuredChainId,
            expectedTokenCodeHash,
            expectedInitCodeHash,
            vm.envOr("CLINK_BUDGET_BROADCAST", false)
        );
    }

    function initCodeHash(address token) public pure returns (bytes32) {
        return keccak256(abi.encodePacked(type(AgentonomyBudgetExecutor).creationCode, abi.encode(token)));
    }

    function _deploy(
        address token,
        uint256 configuredChainId,
        bytes32 expectedTokenCodeHash,
        bytes32 expectedInitCodeHash,
        bool broadcast
    ) internal returns (AgentonomyBudgetExecutor deployed) {
        _validateConfig(token, configuredChainId, expectedTokenCodeHash);
        if (expectedInitCodeHash == bytes32(0)) revert MissingCodeHash();
        if (expectedInitCodeHash != initCodeHash(token)) revert InvalidToken();
        // Validate exact creation bytes INCLUDING constructor argument before broadcast.
        if (broadcast) vm.startBroadcast();
        deployed = new AgentonomyBudgetExecutor(token);
        if (broadcast) vm.stopBroadcast();
        if (deployed.TOKEN() != token || deployed.EXECUTION_CHAIN_ID() != block.chainid) revert InvalidToken();
        emit BudgetExecutorDeployed(address(deployed), token, block.chainid);
    }

    function _validateConfig(address token, uint256 configuredChainId, bytes32 expectedTokenCodeHash) internal view {
        if (token == address(0)) revert MissingToken();
        if (configuredChainId != block.chainid) revert WrongChain();
        if (expectedTokenCodeHash == bytes32(0)) revert MissingCodeHash();
        if (token.code.length == 0 || token.codehash != expectedTokenCodeHash) revert InvalidToken();

        (bool decimalsSuccess, bytes memory decimalsData) = token.staticcall(abi.encodeWithSignature("decimals()"));
        if (!decimalsSuccess || decimalsData.length != 32 || abi.decode(decimalsData, (uint256)) != 6) {
            revert InvalidToken();
        }

        (bool symbolSuccess, bytes memory symbolData) = token.staticcall(abi.encodeWithSignature("symbol()"));
        if (!symbolSuccess || keccak256(symbolData) != keccak256(abi.encode("TestUSD"))) {
            revert InvalidToken();
        }
    }
}
