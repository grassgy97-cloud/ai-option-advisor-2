from __future__ import annotations

import json
import requests

BASE_URL = "http://127.0.0.1:8010"


def main():
    payload = {
        "underlying_id": "510300",
        "hands": 2,
        "dte_min": 60,
        "dte_max": 180,
        "delta_target": 0.20,
        "delta_tolerance": 0.12,
        "max_rel_spread": 0.05,
        "fee_per_share": 0.0004,
        "top_n": 5,
        "target_upside_rules": [
            {"dte_max": 120, "target_upside_buffer": 0.08},
            {"dte_max": 9999, "target_upside_buffer": 0.10}
        ]
    }

    resp = requests.post(f"{BASE_URL}/advisor/covered-call", json=payload, timeout=120)
    print("status_code =", resp.status_code)
    resp.raise_for_status()

    data = resp.json()
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()