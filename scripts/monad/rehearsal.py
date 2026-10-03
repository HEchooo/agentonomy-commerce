"""Run a complete real local-chain purchase and persist only public evidence."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from examples.monad_commerce.rehearsal import LocalRehearsal, CSV


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with LocalRehearsal() as runtime:
        before = runtime.request("snapshot")
        preview = runtime.request("preview", {"offering_id": "csv-reconciliation-v1", "csv_text": CSV, "idempotency_key": "rehearsal-1"})
        purchase = runtime.execute(preview["preview_id"])
        if purchase["state"] != "delivered":
            raise RuntimeError("purchase did not deliver: " + purchase["state"])
        after = runtime.request("snapshot")
        runtime.restart()
        replay = runtime.execute(preview["preview_id"])
        restored = runtime.request("snapshot")
        if replay["purchase_id"] != purchase["purchase_id"] or restored["used_amount_usdc"] != after["used_amount_usdc"]:
            raise RuntimeError("restart replay changed the purchase budget")
        result = dict(mode="local_anvil", public_testnet_acceptance=False, before=before,
                      purchase=purchase, after=after, restart_replay=restored,
                      revocation=runtime.revoke())
    encoded = json.dumps(result, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n")
    print(encoded)

if __name__ == "__main__":
    main()
