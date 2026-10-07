// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.24;

/// @notice Testnet-only fixed-supply TestUSD faucet with no issuer controls.
/// @dev The faucet pool is the token contract itself; claims only transfer from that pool.
contract AgentonomyFaucetUSD {
    string public constant name = "Agentonomy Test USD Faucet - No Value";
    string public constant symbol = "TestUSD";
    uint8 public constant decimals = 6;
    uint256 public constant CLAIM_AMOUNT = 1_000_000;
    uint256 public immutable totalSupply;

    mapping(address account => uint256) public balanceOf;
    mapping(address owner => mapping(address spender => uint256)) public allowance;
    mapping(address account => bool) public claimed;

    event Transfer(address indexed from, address indexed to, uint256 amount);
    event Approval(address indexed owner, address indexed spender, uint256 amount);

    error AlreadyClaimed();
    error InsufficientAllowance();
    error InsufficientPool();
    error UnsupportedChain(uint256 chainId);
    error ZeroAddress();
    error ZeroAmount();

    constructor() {
        uint256 chainId = block.chainid;
        if (chainId != 10_143 && chainId != 31_337) revert UnsupportedChain(chainId);

        totalSupply = 1_000 * CLAIM_AMOUNT;
        balanceOf[address(this)] = totalSupply;
        emit Transfer(address(0), address(this), totalSupply);
    }

    /// @notice Transfer one faucet unit to the calling address, once per address.
    function claim() external {
        address recipient = msg.sender;
        if (recipient == address(0)) revert ZeroAddress();
        if (claimed[recipient]) revert AlreadyClaimed();
        if (balanceOf[address(this)] < CLAIM_AMOUNT) revert InsufficientPool();

        // Mark before moving the pool balance. No external call occurs in this function.
        claimed[recipient] = true;
        balanceOf[address(this)] -= CLAIM_AMOUNT;
        balanceOf[recipient] += CLAIM_AMOUNT;
        emit Transfer(address(this), recipient, CLAIM_AMOUNT);
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        if (spender == address(0)) revert ZeroAddress();
        allowance[msg.sender][spender] = amount;
        emit Approval(msg.sender, spender, amount);
        return true;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        _transfer(msg.sender, to, amount);
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        uint256 currentAllowance = allowance[from][msg.sender];
        if (currentAllowance < amount) revert InsufficientAllowance();
        allowance[from][msg.sender] = currentAllowance - amount;
        _transfer(from, to, amount);
        return true;
    }

    function _transfer(address from, address to, uint256 amount) internal {
        if (from == address(0) || to == address(0)) revert ZeroAddress();
        if (amount == 0) revert ZeroAmount();
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
        emit Transfer(from, to, amount);
    }
}
