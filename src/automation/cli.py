from __future__ import annotations

import argparse
import json
import sys

from automation.self_healing import run_self_healing
from core.config import load_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a controlled self-healing failure drill.")
    parser.add_argument("--trigger", default="cli failure drill", help="Human-readable reason for this run.")
    parser.add_argument(
        "--repair-source",
        choices=("auto", "snapshot", "live"),
        default="auto",
        help="Select raw snapshot, live Crossref, or automatic source selection.",
    )
    args = parser.parse_args()

    result = run_self_healing(load_settings(), trigger=args.trigger, repair_source=args.repair_source)
    print(json.dumps(result.__dict__, indent=2))
    if not result.success:
        sys.exit(1)


if __name__ == "__main__":
    main()
