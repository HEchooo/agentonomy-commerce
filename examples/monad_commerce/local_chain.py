"""Ephemeral Anvil rehearsal. All generated keys exist only in this process.

Never accepts a remote RPC URL or uses a public chain. Not a wallet service.
"""
from __future__ import annotations

import json
from pathlib import Path
import socket
import subprocess
import time

from eth_account import Account
from eth_abi import encode
from eth_utils import keccak, to_checksum_address
import httpx

from agentonomy_commerce.budget_network import RpcClient, quantity

ROOT = Path(__file__).resolve().parents[2]


def calldata(signature: str, types: list, values: list) -> str:
    return "0x" + (keccak(text=signature)[:4] + encode(types, values)).hex()


class LocalChain:
    def __init__(self):
        self.owner = Account.create()
        self.execution_signer = Account.create()
        self.relayer = Account.create()
        self.payee = Account.create().address
        self.process = None
        self.token = self.executor = None

    def __enter__(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        self.rpc = RpcClient(self.url, writable=True)
        self.process = subprocess.Popen(
            ["anvil", "--host", "127.0.0.1", "--port", str(port), "--chain-id", "31337",
             "--slots-in-an-epoch", "1", "--silent"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(100):
                if self.process.poll() is not None:
                    raise RuntimeError("local Anvil exited")
                try:
                    self.rpc.check_chain(31337)
                    break
                except RuntimeError:
                    time.sleep(.05)
            else:
                raise RuntimeError("local Anvil did not start")
            for wallet in (self.owner, self.relayer):
                self._local_call("anvil_setBalance", [wallet.address, hex(100 * 10**18)])
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def _local_call(self, method, params):
        if self.process is None or self.process.poll() is not None:
            raise RuntimeError("owned local chain is not running")
        with httpx.Client(trust_env=False, timeout=5) as client:
            response = client.post(self.url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).json()
        if "error" in response:
            raise RuntimeError("local Anvil operation failed")
        return response["result"]

    def transact(self, wallet, *, to=None, data="0x", gas=2_000_000):
        self.rpc.check_chain(31337)
        tx = dict(chainId=31337, nonce=quantity(self.rpc.call("eth_getTransactionCount", [wallet.address, "pending"])),
                  value=0, gas=gas, gasPrice=quantity(self.rpc.call("eth_gasPrice", [])), data=data)
        if to:
            tx["to"] = to_checksum_address(to)
        signed = wallet.sign_transaction(tx)
        tx_hash = self.rpc.call("eth_sendRawTransaction", ["0x" + signed.raw_transaction.hex()])
        receipt = self.rpc.call("eth_getTransactionReceipt", [tx_hash])
        if not receipt or receipt.get("status") != "0x1":
            raise RuntimeError("local transaction reverted or not mined")
        return receipt

    def deploy(self):
        def bytecode(name):
            artifact = ROOT / "contracts" / "out" / f"{name}.sol" / f"{name}.json"
            if not artifact.exists():
                raise RuntimeError("run forge build inside contracts before local rehearsal")
            return json.loads(artifact.read_text())["bytecode"]["object"].removeprefix("0x")
        token = self.transact(self.owner, data="0x" + bytecode("AgentonomyTestUSD") + encode(
            ["address", "uint256"], [self.owner.address, 20_000_000]).hex())
        self.token = token["contractAddress"]
        executor = self.transact(self.owner, data="0x" + bytecode("AgentonomyBudgetExecutor") + encode(
            ["address"], [self.token]).hex())
        self.executor = executor["contractAddress"]
        return self.token, self.executor

    def approve(self, amount):
        return self.transact(self.owner, to=self.token, data=calldata(
            "approve(address,uint256)", ["address", "uint256"], [self.executor, amount]))

    def mine(self, blocks=1):
        self._local_call("anvil_mine", [hex(blocks)])

    def __exit__(self, *_):
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
            self.process = None
