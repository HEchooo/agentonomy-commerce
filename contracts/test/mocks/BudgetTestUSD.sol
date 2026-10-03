// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.24;

interface BudgetExecutorMarkers {
    function grantDigests(address owner, bytes32 grantId) external view returns (bytes32);
    function paid(address owner, bytes32 purchaseId) external view returns (bool);
    function spent(bytes32 grantHash) external view returns (uint256);
}

/// @notice Deliberately small ERC-20-like test asset with adversarial modes.
/// @dev This asset is only for local contract tests; it is not official USDC.
contract BudgetTestUSD {
    enum Mode {
        Success,
        False,
        Malformed,
        Reverting,
        Reentrant,
        FeeOnTransfer,
        NoOwnerDebit,
        NoPayeeCredit,
        MalformedBalance,
        ObserveEffects
    }

    mapping(address account => uint256) private _balances;
    mapping(address account => mapping(address spender => uint256)) public allowance;

    address public executor;
    Mode public mode;
    bytes public reentryData;
    bool public reentryAttempted;
    bool public reentrySucceeded;
    bytes32 public observedGrantHash;
    bytes32 public observedGrantId;
    bytes32 public observedPurchaseId;
    address public observedOwner;
    bool public effectsObserved;

    function configure(address executorAddress, Mode newMode) external {
        executor = executorAddress;
        mode = newMode;
        reentryAttempted = false;
        reentrySucceeded = false;
        effectsObserved = false;
    }

    function configureObservation(bytes32 grantHash, address owner, bytes32 grantId, bytes32 purchaseId) external {
        observedGrantHash = grantHash;
        observedOwner = owner;
        observedGrantId = grantId;
        observedPurchaseId = purchaseId;
        effectsObserved = false;
    }

    function setReentry(bytes calldata data) external {
        reentryData = data;
    }

    function mint(address account, uint256 amount) external {
        _balances[account] += amount;
    }

    function burn(address account, uint256 amount) external {
        _balances[account] -= amount;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        require(msg.sender == executor, "not executor");
        if (mode == Mode.Reverting) revert("transfer failed");
        if (mode == Mode.False) return false;
        if (mode == Mode.Malformed) {
            assembly {
                mstore(0, 1)
                return(0, 1)
            }
        }
        if (mode == Mode.Reentrant) {
            reentryAttempted = true;
            (reentrySucceeded,) = executor.call(reentryData);
        }
        if (mode == Mode.ObserveEffects) {
            effectsObserved = BudgetExecutorMarkers(executor).spent(observedGrantHash) == amount
                && BudgetExecutorMarkers(executor).paid(observedOwner, observedPurchaseId)
                && BudgetExecutorMarkers(executor).grantDigests(observedOwner, observedGrantId) != bytes32(0);
        }

        if (mode != Mode.NoOwnerDebit) {
            require(allowance[from][msg.sender] >= amount, "allowance");
            allowance[from][msg.sender] -= amount;
            _balances[from] -= amount;
        }
        if (mode == Mode.NoOwnerDebit) {
            // Keep the call successful while deliberately violating the debit invariant.
            return true;
        }
        if (mode == Mode.FeeOnTransfer) {
            _balances[to] += amount - 1;
        } else if (mode != Mode.NoPayeeCredit) {
            _balances[to] += amount;
        }
        return true;
    }

    function balanceOf(address account) external view returns (uint256) {
        if (mode == Mode.MalformedBalance) {
            assembly {
                mstore(0, 1)
                return(0, 1)
            }
        }
        return _balances[account];
    }
}
