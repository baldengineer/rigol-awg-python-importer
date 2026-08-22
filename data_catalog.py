"""Query the Rigol DG1022 waveform-memory catalog over VISA."""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time

from rigol_dg1022.visa import DEFAULT_TIMEOUT_MS, VisaConnection, list_resources
from rigol_dg1022.names import validate_waveform_name, validate_waveform_reference


USER_CATALOG_QUERY = "DATA:NVOLatile:CATalog?"
FREE_SLOTS_QUERY = "DATA:NVOL:FREE?"
FULL_CATALOG_QUERY = "DATA:CATalog?"
POINTS_QUERY = "DATA:ATTRibute:POINts?"
BUILT_IN_NAMES = frozenset({"EXP_RISE", "EXP_FALL", "NEG_RAMP", "SINC", "CARDIAC"})
SCPI_SETTLE_SECONDS = 2.0
BYTES_PER_POINT = 2


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


def _require_no_scpi_error(connection: VisaConnection, stage: str) -> None:
    status = connection.query("SYST:ERR?")
    match = re.match(r"^\s*([+-]?\d+)(?:\s*,|\s*$)", status)
    if match is None:
        raise RuntimeError(f"Unparseable SCPI error response after {stage}: {status}")
    if int(match.group(1)) != 0:
        raise RuntimeError(f"SCPI error after {stage}: {status}")


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


def query_waveform_byte_counts(
    resource: str,
    names: tuple[str, ...],
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> dict[str, int | None]:
    """Return stored byte counts, or None when the instrument cannot report one."""
    counts: dict[str, int | None] = {}
    with VisaConnection(resource, timeout_ms) as connection:
        try:
            for name in names:
                if name.upper() in BUILT_IN_NAMES:
                    counts[name] = None
                    continue
                response = connection.query(f"{POINTS_QUERY} {name}")
                try:
                    points = int(response.strip())
                except (AttributeError, ValueError):
                    # DG1022 firmware reports "Invalid Command" for built-in
                    # waveform names. Clear that expected error before the
                    # next query and leave their size unavailable.
                    try:
                        connection.query("SYST:ERR?")
                    except RuntimeError:
                        pass
                    counts[name] = None
                else:
                    if points < 0:
                        raise ValueError(f"negative point count for {name!r}: {response!r}")
                    counts[name] = points * BYTES_PER_POINT
            return counts
        finally:
            try:
                connection.write("SYST:LOC")
            except RuntimeError:
                pass


def rename_waveform(
    resource: str,
    old_name: str,
    new_name: str,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> tuple[str, ...]:
    """Rename a stored waveform and return the verified nonvolatile catalog."""
    validate_waveform_reference(old_name, "old name")
    validate_waveform_name(new_name, "new name")
    if old_name == new_name:
        raise ValueError("old and new names must be different")

    with VisaConnection(resource, timeout_ms) as connection:
        try:
            connection.write(f"DATA:RENAME {old_name},{new_name}")
            time.sleep(SCPI_SETTLE_SECONDS)
            _require_no_scpi_error(connection, f"renaming {old_name} to {new_name}")
            names = _query_catalog(connection, USER_CATALOG_QUERY)
            normalized_names = {name.upper() for name in names}
            if new_name.upper() not in normalized_names or old_name.upper() in normalized_names:
                raise RuntimeError(
                    f"rename verification failed; nonvolatile catalog is {names!r}"
                )
            return names
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


def _format_waveform_name(name: str, byte_counts: dict[str, int | None]) -> str:
    byte_count = byte_counts.get(name)
    if byte_count is None:
        return f"{name}: size unavailable"
    points = byte_count // BYTES_PER_POINT
    return f"{name}: {points:,} Points ({byte_count:,} bytes)"


def print_full_catalog(
    names: tuple[str, ...],
    free_slots: int | None = None,
    byte_counts: dict[str, int | None] | None = None,
) -> None:
    """Print the full catalog grouped into built-in and remaining entries."""
    byte_counts = byte_counts or {}
    built_in = tuple(name for name in names if name.upper() in BUILT_IN_NAMES)
    custom = tuple(name for name in names if name.upper() not in BUILT_IN_NAMES)
    if free_slots is not None and free_slots > 0:
        print(f"Free nonvolatile slots: {free_slots}")
        print()
    print("Built-In:")
    print(*built_in, sep="\n")
    print()
    print("Custom:")
    print(*(_format_waveform_name(name, byte_counts) for name in custom), sep="\n")


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
    parser.add_argument(
        "--rename",
        nargs=2,
        metavar=("OLD", "NEW"),
        help="rename a nonvolatile waveform, for example --rename 000 0_First",
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
    if args.rename and not args.resource:
        print("--rename requires --resource.", file=sys.stderr)
        return 2
    if args.rename and args.all:
        print("--rename cannot be combined with --all.", file=sys.stderr)
        return 2

    try:
        if args.list_resources:
            resources = list_resources()
            print(*resources, sep="\n")
        if args.resource:
            if args.rename:
                old_name, new_name = args.rename
                names = rename_waveform(
                    args.resource,
                    old_name,
                    new_name,
                    args.timeout_ms,
                )
                byte_counts = query_waveform_byte_counts(
                    args.resource,
                    names,
                    args.timeout_ms,
                )
                print(f"Renamed {old_name} to {new_name}.")
                print("Nonvolatile waveforms:")
                print(*(_format_waveform_name(name, byte_counts) for name in names), sep="\n")
                return 0
            if args.all:
                names = query_all_waveform_names(args.resource, args.timeout_ms)
                free_slots = query_free_slots(args.resource, args.timeout_ms)
                byte_counts = query_waveform_byte_counts(
                    args.resource,
                    names,
                    args.timeout_ms,
                )
            else:
                names = query_user_memory_names(args.resource, args.timeout_ms)
                free_slots = None
                byte_counts = query_waveform_byte_counts(
                    args.resource,
                    names,
                    args.timeout_ms,
                ) if names else {}
            if args.all:
                print_full_catalog(names, free_slots, byte_counts)
            elif not names:
                print("No saved user waveforms found.")
            else:
                print(*(_format_waveform_name(name, byte_counts) for name in names), sep="\n")
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Rigol communication failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# SPDX-License-Identifier: MIT
# Copyright (c) 2026 James Lewis
