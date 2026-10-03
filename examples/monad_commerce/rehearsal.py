"""Local real-EVM rehearsal shared by the CLI, browser and unified MCP."""
from __future__ import annotations
from pathlib import Path
import tempfile
import threading
from time import monotonic, sleep

from examples.commerce.node import MarketplaceBridge
from examples.monad_commerce.local_chain import LocalChain, calldata

CSV = "transaction_id,date,description,amount,currency,category\n1,2026-10-03,Sale,10.00,USD,sales\n2,2026-10-03,Fee,-2.00,USD,fees\n"


def public_purchase(value):
    allowed = {"purchase_id", "preview_id", "offering_id", "state", "execution_mode",
               "reason_code", "receipt_id", "input_hash", "output_hash", "created_at", "updated_at", "service_result", "settlement"}
    return {key: item for key, item in value.items() if key in allowed}


class LocalRehearsal:
    def __enter__(self):
        self.bridge = None
        self.chain = LocalChain()
        self.stop = threading.Event()
        self.directory = tempfile.TemporaryDirectory(prefix="agentonomy-local-chain-")
        try:
            self.chain.__enter__()
            self.chain.deploy()
            approval = self.chain.approve(1_000_000)
            self.chain.mine(5)
            self.bootstrap = dict(mode="local_anvil", chain_id=31337,
                rpc_urls=[self.chain.url, self.chain.url], token=self.chain.token,
                executor=self.chain.executor, payee=self.chain.payee,
                owner_key="0x" + self.chain.owner.key.hex(),
                execution_key="0x" + self.chain.execution_signer.key.hex(),
                relayer_key="0x" + self.chain.relayer.key.hex(), approval_tx=approval["transactionHash"])
            self.restart()
            self.miner = threading.Thread(target=self._mine, daemon=True)
            self.miner.start()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def _mine(self):
        while not self.stop.wait(.4):
            try:
                self.chain.mine(3)
            except Exception:
                self.stop.set()

    def restart(self):
        if self.bridge:
            self.bridge.close()
        self.bridge = MarketplaceBridge(Path(self.directory.name), worker_module="examples.monad_commerce.market_worker")
        self.bridge.request("initialize", self.bootstrap)

    def request(self, method, args=None):
        return self.bridge.request(method, args)

    def execute(self, preview_id, *, wait_seconds=8):
        deadline = monotonic() + wait_seconds
        while True:
            result = self.request("execute", {"preview_id": preview_id})
            if result.get("state") != "payment_submitted" or monotonic() >= deadline:
                return public_purchase(result)
            sleep(.5)

    def revoke(self):
        snapshot = self.request("snapshot")
        self.request("revoke")
        tx = self.chain.transact(self.chain.owner, to=self.chain.executor,
            data=calldata("revoke(bytes32)", ["bytes32"], [bytes.fromhex(snapshot["chain_grant_id"][2:])]))
        self.chain.mine(5)
        return {"core_revoked": True, "chain_revoked": True, "transaction_hash": tx["transactionHash"], "mode": "local_anvil"}

    def __exit__(self, *_):
        self.stop.set()
        if getattr(self, "miner", None):
            self.miner.join(timeout=3)
        if self.bridge:
            self.bridge.close()
        self.chain.__exit__(None, None, None)
        self.bootstrap = None
        self.directory.cleanup()
