"""Discover a Rigol DG1022 and query its identity over VISA."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

# Allow the documented ``python .\\tools\\rigol_idn.py`` invocation from the
# repository root before the package has been installed into the environment.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rigol_dg1022.visa import DEFAULT_TIMEOUT_MS, idn, list_resources


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List VISA resources or query *IDN? on a Rigol DG1022."
    )
    parser.add_argument(
        "--list-resources",
        action="store_true",
        help="list resources detected by the VISA backend",
    )
    parser.add_argument(
        "--resource",
        help="VISA resource string, for example USB0::0x1AB1::...::INSTR",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=DEFAULT_TIMEOUT_MS,
        help=f"VISA timeout in milliseconds (default: {DEFAULT_TIMEOUT_MS})",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.list_resources and not args.resource:
        print("Provide --resource or --list-resources.", file=sys.stderr)
        return 2
    if args.timeout_ms <= 0:
        print("--timeout-ms must be positive.", file=sys.stderr)
        return 2

    try:
        if args.list_resources:
            resources = list_resources()
            print(*resources, sep="\n")
        if args.resource:
            print(idn(args.resource, args.timeout_ms))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Rigol communication failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# SPDX-License-Identifier: MIT
# Copyright (c) 2026 James Lewis
