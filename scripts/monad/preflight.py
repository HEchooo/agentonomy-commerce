"""Read-only deployment checklist. This command cannot sign or broadcast."""
from __future__ import annotations
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from eth_abi import decode, encode
from eth_utils import keccak
from agentonomy_commerce.budget_network import NetworkConfig, RpcClient, quantity, verified_boundary


def inspect_configuration(value: dict, *, rpc_check: bool = False) -> dict:
    required = {"mode", "chain_id", "rpc_urls", "token", "executor", "payee"}
    report = dict(broadcast=False, rpc_checked=False, public_acceptance=False,
                  missing=sorted(key for key in required if not value.get(key)))
    artifacts = {}
    for name in ("AgentonomyTestUSD", "AgentonomyBudgetExecutor"):
        path = ROOT / "contracts" / "out" / f"{name}.sol" / f"{name}.json"
        if path.exists():
            artifact = json.loads(path.read_text())
            code = bytes.fromhex(artifact["bytecode"]["object"].removeprefix("0x"))
            artifacts[name] = {"creation_bytecode_sha256": hashlib.sha256(code).hexdigest(), "bytes": len(code)}
            if name == "AgentonomyBudgetExecutor" and value.get("token"):
                artifacts[name]["init_code_keccak256"] = "0x" + keccak(code + encode(["address"], [value["token"]])).hex()
        else:
            artifacts[name] = {"missing": "run forge build in contracts"}
    report["artifacts"] = artifacts
    if report["missing"]:
        return report | {"status": "needs_configuration",
                         "fee_estimate": "unavailable until deployment and signer scope is configured"}
    config = NetworkConfig(**{key: value[key] for key in NetworkConfig.__dataclass_fields__ if key in value})
    report.update(status="configuration_valid", mode=config.mode, chain_id=config.chain_id,
                  token=config.token, executor=config.executor, payee=config.payee,
                  gas_limit_cap=config.gas_limit, gas_price_cap_wei=str(config.max_gas_price_wei),
                  maximum_gas_cost_wei=str(config.gas_limit * config.max_gas_price_wei),
                  required_signers=["wallet owner", "execution signer", "gas relayer"],
                  finality_rule="finalized_minus_3" if config.mode == "monad_testnet" else "local_finalized")
    if not rpc_check:
        return report
    observations = []
    clients = [RpcClient(url) for url in config.rpc_urls]
    boundaries = []
    for rpc in clients:
        rpc.check_chain(config.chain_id)
        block = rpc.call("eth_getBlockByNumber", ["finalized", False])
        boundaries.append(verified_boundary(config, quantity(block["number"])))
    boundary = min(boundaries)
    for rpc in clients:
        canonical = rpc.call("eth_getBlockByNumber", [hex(boundary), False])
        if not canonical or quantity(canonical["number"]) != boundary:
            raise ValueError("RPC canonical boundary height mismatch")
        block_hash = canonical.get("hash")
        if not isinstance(block_hash, str) or not block_hash.startswith("0x") or len(block_hash) != 66:
            raise ValueError("RPC canonical boundary hash invalid")
        try:
            if int(block_hash[2:], 16) == 0:
                raise ValueError("RPC canonical boundary hash invalid")
        except ValueError:
            raise ValueError("RPC canonical boundary hash invalid") from None
        canonical_boundary = {"number": boundary, "hash": block_hash.lower()}
        hashes = {}
        for name, deployed_address in (("token", config.token), ("executor", config.executor)):
            code = rpc.call("eth_getCode", [deployed_address, hex(boundary)])
            if not isinstance(code, str) or code == "0x":
                raise ValueError(f"{name} contract has not been deployed")
            hashes[name] = "0x" + keccak(bytes.fromhex(code[2:])).hex()
            expected = value.get(name + "_code_hash")
            if expected is None or hashes[name] != expected:
                raise ValueError(f"{name} code hash requires an exact approved manifest match")
        def call(to, signature, output):
            result = rpc.call("eth_call", [{"to": to, "data": "0x" + keccak(text=signature)[:4].hex()}, hex(boundary)])
            return decode([output], bytes.fromhex(result[2:]))[0]
        if call(config.token, "decimals()", "uint8") != 6 or call(config.token, "symbol()", "string") != "TestUSD":
            raise ValueError("declared test asset identity mismatch")
        if call(config.executor, "TOKEN()", "address").lower() != config.token:
            raise ValueError("executor token mismatch")
        if call(config.executor, "EXECUTION_CHAIN_ID()", "uint256") != config.chain_id:
            raise ValueError("executor immutable chain mismatch")
        observations.append({"verified_boundary": boundary, "canonical_boundary": canonical_boundary, "code_hashes": hashes})
    if observations[0]["canonical_boundary"] != observations[1]["canonical_boundary"]:
        raise ValueError("RPC canonical boundary observations disagree")
    if observations[0]["code_hashes"] != observations[1]["code_hashes"]:
        raise ValueError("RPC deployment observations disagree")
    return report | {"status": "deployed_configuration_verified", "rpc_checked": True,
                     "observations": observations}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--rpc-check", action="store_true")
    args = parser.parse_args()
    try:
        report = inspect_configuration(json.loads(args.config.read_text()), rpc_check=args.rpc_check)
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        print(json.dumps({"status": "blocked", "broadcast": False, "reason": str(exc)}))
        return 2
    print(json.dumps(report, indent=2))
    return 2 if report["status"] == "needs_configuration" else 0

if __name__ == "__main__":
    raise SystemExit(main())
