"""Query the Rigol DG1022 waveform-memory catalog over VISA."""

from __future__ import annotations

import argparse
import csv
import sys

from rigol_dg1022.visa import DEFAULT_TIMEOUT_MS, VisaConnection, list_resources


USER_CATALOG_QUERY = "DATA:NVOLatile:CATalog?"
FREE_SLOTS_QUERY = "DATA:NVOL:FREE?"
FULL_CATALOG_QUERY = "DATA:CATalog?"
BUILT_IN_NAMES = frozenset({"EXP_RISE", "EXP_FALL", "NEG_RAMP", "SINC", "CARDIAC"})


def parse_catalog_response(response: str) -> tuple[str, ...]:
    """Parse a DG1000 comma-separated, optionally quoted catalog response."""
    if not isinstance(response, str) or not response.strip():
        raise ValueError("catalog response must be a non-empty string")
    normalized = response.strip()
    if normalized == '""':
        return ()
    # Some DG1022 firmware appends a comma to the NVOL catalog response.
    if normalized.endswith(","):
        normalized = normalized[:-1].rstrip()
    try:
        rows = list(csv.reader([normalized], skipinitialspace=True, strict=True))
    except csv.Error as exc:
        raise ValueError(f"invalid catalog response: {response!r}") from exc
    if len(rows) != 1 or any(not name for name in rows[0]):
        raise ValueError(f"invalid catalog response: {response!r}")
    return tuple(name.strip() for name in rows[0])


def _query_catalog(connection: VisaConnection, command: str) -> tuple[str, ...]:
    return parse_catalog_response(connection.query(command))


def parse_free_slots_response(response: str) -> int:
    """Parse the DG1000 count of available nonvolatile waveform slots."""
    try:
        free_slots = int(response.strip())
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"invalid free-slot response: {response!r}") from exc
    if not 0 <= free_slots <= 10:
        raise ValueError(f"free-slot response must be between 0 and 10: {response!r}")
    return free_slots


def query_user_memory_names(resource: str, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> tuple[str, ...]:
    """Return names stored in the DG1022's nonvolatile waveform memories."""
    with VisaConnection(resource, timeout_ms) as connection:
        try:
            free_slots = parse_free_slots_response(connection.query(FREE_SLOTS_QUERY))
            if free_slots == 10:
                return ()
            return _query_catalog(connection, USER_CATALOG_QUERY)
        finally:
            try:
                connection.write("SYST:LOC")
            except RuntimeError:
                pass


def query_free_slots(resource: str, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> int:
    """Return the number of unused nonvolatile waveform-memory slots."""
    with VisaConnection(resource, timeout_ms) as connection:
        try:
            return parse_free_slots_response(connection.query(FREE_SLOTS_QUERY))
        finally:
            try:
                connection.write("SYST:LOC")
            except RuntimeError:
                pass


def query_all_waveform_names(resource: str, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> tuple[str, ...]:
    """Return built-in, volatile, and nonvolatile waveform names."""
    with VisaConnection(resource, timeout_ms) as connection:
        try:
            return _query_catalog(connection, FULL_CATALOG_QUERY)
        finally:
            try:
                connection.write("SYST:LOC")
            except RuntimeError:
                pass


def print_full_catalog(names: tuple[str, ...], free_slots: int | None = None) -> None:
    """Print the full catalog grouped into built-in and remaining entries."""
    built_in = tuple(name for name in names if name.upper() in BUILT_IN_NAMES)
    custom = tuple(name for name in names if name.upper() not in BUILT_IN_NAMES)
    if free_slots is not None and free_slots > 0:
        print(f"Free nonvolatile slots: {free_slots}")
        print()
    print("Built-In:")
    print(*built_in, sep="\n")
    print()
    print("Custom:")
    print(*custom, sep="\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Query waveform names stored in a Rigol DG1022 data memory."
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
    parser.add_argument(
        "--all",
        action="store_true",
        help="query the full catalog, including built-in and VOLATILE waveforms",
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
            if args.all:
                names = query_all_waveform_names(args.resource, args.timeout_ms)
                free_slots = query_free_slots(args.resource, args.timeout_ms)
            else:
                names = query_user_memory_names(args.resource, args.timeout_ms)
                free_slots = None
            if args.all:
                print_full_catalog(names, free_slots)
            elif not names:
                print("No saved user waveforms found.")
            else:
                print(*names, sep="\n")
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Rigol communication failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# SPDX-License-Identifier: MIT
# Copyright (c) 2026 James Lewis
