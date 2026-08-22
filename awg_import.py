"""Import ArbDraw JSON or CSV waveforms for the Rigol DG1022.

The command-line interface mirrors the OWON reference importer.  Parsing,
validation, discovery, identity, output control, and dry-runs are implemented;
the model-specific binary upload is intentionally gated until it is validated
against a connected DG1022.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import struct
import sys
import tomllib
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from rigol_dg1022.visa import DEFAULT_TIMEOUT_MS, VisaConnection, idn, list_resources


DEFAULTS_FILE = "defaults.toml"


@dataclass(frozen=True)
class Config:
    expected_identity_prefix: str = ""
    max_point_count: int = 16384
    max_dac_code: int = 16383
    timeout_ms: int = DEFAULT_TIMEOUT_MS

    @classmethod
    def from_defaults(cls, values: dict[str, Any]) -> "Config":
        prefix = values.get("expected_identity_prefix", "")
        points = values.get("max_point_count", cls.max_point_count)
        dac = values.get("max_dac_code", cls.max_dac_code)
        timeout = values.get("timeout_ms", cls.timeout_ms)
        if not isinstance(prefix, str):
            raise ValueError("expected_identity_prefix must be a string")
        if isinstance(points, bool) or not isinstance(points, int) or points < 2:
            raise ValueError("max_point_count must be an integer of at least 2")
        if isinstance(dac, bool) or not isinstance(dac, int) or dac <= 0:
            raise ValueError("max_dac_code must be a positive integer")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
            raise ValueError("timeout_ms must be a positive integer")
        return cls(prefix, points, dac, timeout)


def packaged_defaults_resource() -> Any:
    return files("rigol_dg1022").joinpath("defaults.toml")


def load_defaults(path: str | Path | Any) -> dict[str, Any]:
    try:
        if isinstance(path, (str, Path)) and Path(path).name == DEFAULTS_FILE and not Path(path).exists():
            path = packaged_defaults_resource()
        if hasattr(path, "read_text"):
            values = tomllib.loads(path.read_text(encoding="utf-8"))
        else:
            with Path(path).open("rb") as stream:
                values = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"Could not read defaults TOML: {exc}") from exc
    required = {"usb_resource", "timeout_ms", "expected_identity_prefix", "max_point_count", "max_dac_code", "frequency_hz", "voltage_vpp", "offset_voltage", "channel", "enable_output", "persist"}
    missing = sorted(required - values.keys()) if isinstance(values, dict) else ["(table)"]
    if missing:
        raise ValueError(f"Defaults TOML is missing required keys: {', '.join(missing)}")
    Config.from_defaults(values)
    return values


@dataclass(frozen=True)
class Waveform:
    name: str
    waveform_type: str
    values: tuple[float, ...]
    low_voltage: float
    high_voltage: float
    sample_rate_sa: float
    frequency_hz: float

    @property
    def sample_count(self) -> int:
        return len(self.values)

    @property
    def amplitude_vpp(self) -> float:
        return self.high_voltage - self.low_voltage

    @property
    def offset_voltage(self) -> float:
        return (self.high_voltage + self.low_voltage) / 2


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be a finite number")
    return result


def waveform_from_document(document: dict[str, Any], config: Config) -> Waveform:
    if document.get("schema") != "arbdraw.waveform" or document.get("version") != 1:
        raise ValueError("Unsupported ArbDraw schema; expected arbdraw.waveform version 1")
    waveform = document.get("waveform")
    if not isinstance(waveform, dict):
        raise ValueError("Missing waveform object")
    count = math.floor(_number(waveform.get("sampleCount"), "waveform.sampleCount") + 0.5)
    if not 2 <= count <= config.max_point_count:
        raise ValueError(f"waveform.sampleCount must resolve to 2 through {config.max_point_count}")
    values = waveform.get("values")
    if not isinstance(values, list) or len(values) != count:
        raise ValueError("waveform.values length must equal waveform.sampleCount")
    samples = tuple(_number(value, f"waveform.values[{index}]") for index, value in enumerate(values))
    low = _number(waveform.get("lowVoltage"), "waveform.lowVoltage")
    high = _number(waveform.get("highVoltage"), "waveform.highVoltage")
    if high <= low or any(value < low or value > high for value in samples):
        raise ValueError("waveform values must lie within a non-zero lowVoltage/highVoltage range")
    sample_rate = _number(waveform.get("sampleRateMSa"), "waveform.sampleRateMSa")
    frequency = _number(waveform.get("frequencyHz"), "waveform.frequencyHz")
    if sample_rate <= 0 or frequency <= 0:
        raise ValueError("sampleRateMSa and frequencyHz must be greater than zero")
    return Waveform(str(document.get("name", "Imported waveform")), str(waveform.get("type", "custom")), samples, low, high, sample_rate * 1e6, frequency)


def load_arbdraw_json(path: str | Path, config: Config) -> Waveform:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read ArbDraw JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError("ArbDraw project must be a JSON object")
    return waveform_from_document(document, config)


def load_csv(path: str | Path, config: Config, *, delimiter: str = ",", value_column: int | None = None) -> Waveform:
    if len(delimiter) != 1:
        raise ValueError("CSV delimiter must be exactly one character")
    times: list[float] = []
    values: list[float] = []
    try:
        with Path(path).open("r", encoding="utf-8-sig", newline="") as stream:
            for row_number, row in enumerate(csv.reader(stream, delimiter=delimiter), 1):
                if not row or all(not cell.strip() for cell in row):
                    continue
                if value_column is None and len(row) == 2:
                    time_value, value = row
                elif value_column is None and len(row) == 1:
                    time_value, value = len(values), row[0]
                elif value_column is not None and 0 <= value_column < len(row):
                    time_value, value = len(values), row[value_column]
                else:
                    raise ValueError(f"CSV row {row_number} has no selected voltage column")
                times.append(_number(time_value, f"CSV row {row_number} time"))
                values.append(_number(value, f"CSV row {row_number} voltage"))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ValueError(f"Could not read CSV: {exc}") from exc
    if not 2 <= len(values) <= config.max_point_count:
        raise ValueError(f"CSV must contain 2 through {config.max_point_count} samples")
    intervals = [b - a for a, b in zip(times, times[1:])]
    if any(interval <= 0 for interval in intervals):
        raise ValueError("CSV timestamps must be strictly increasing")
    interval = sum(intervals) / len(intervals)
    if any(abs(candidate - interval) > max(abs(interval) * 1e-6, 1e-15) for candidate in intervals):
        raise ValueError("CSV timestamps must be uniformly spaced")
    low, high = min(values), max(values)
    if high <= low:
        raise ValueError("CSV voltages must span more than one value")
    rate = 1 / interval
    return Waveform(Path(path).stem, "csv", tuple(values), low, high, rate, rate / len(values))


def load_waveform(path: str | Path, config: Config, *, csv_delimiter: str = ",", csv_value_column: int | None = None) -> Waveform:
    return load_csv(path, config, delimiter=csv_delimiter, value_column=csv_value_column) if Path(path).suffix.lower() == ".csv" else load_arbdraw_json(path, config)


def encode_dab(waveform: Waveform, config: Config) -> bytes:
    return struct.pack(f">{waveform.sample_count}H", *[
        int(max(0, min(1, (value - waveform.low_voltage) / waveform.amplitude_vpp)) * config.max_dac_code + 0.5)
        for value in waveform.values
    ])


def make_ieee_block(payload: bytes) -> bytes:
    length = str(len(payload)).encode("ascii")
    return b"#" + str(len(length)).encode("ascii") + length + payload


def set_output_state(resource: str, timeout_ms: int, channel: int, enabled: bool) -> None:
    with VisaConnection(resource, timeout_ms) as connection:
        connection.write(f"OUTP{channel} {'ON' if enabled else 'OFF'}")


def parse_args() -> argparse.Namespace:
    defaults_parser = argparse.ArgumentParser(add_help=False)
    defaults_parser.add_argument("--defaults-file", type=Path, default=DEFAULTS_FILE)
    preliminary, _ = defaults_parser.parse_known_args()
    try:
        defaults = load_defaults(preliminary.defaults_file)
    except ValueError as exc:
        defaults_parser.error(str(exc))
    parser = argparse.ArgumentParser(description="Import an ArbDraw JSON or headerless x,y CSV waveform into a Rigol DG1022 over VISA.")
    parser.add_argument("--defaults-file", type=Path, default=preliminary.defaults_file, help=f"TOML defaults file (default: {DEFAULTS_FILE})")
    parser.add_argument("waveform_file", type=Path, nargs="?", help="ArbDraw JSON or headerless x,y CSV waveform file")
    parser.add_argument("--csv-delimiter", default=",", help="CSV field delimiter (default: comma)")
    parser.add_argument("--csv-column", dest="csv_value_column", type=int, metavar="INDEX", help="zero-based CSV voltage column")
    parser.add_argument("--list-resources", action="store_true", help="list detected VISA resource strings")
    parser.add_argument("--idn", metavar="RESOURCE", help="query *IDN? on a VISA resource")
    parser.add_argument("--output", choices=("on", "off"), help="send only an output state command to --channel")
    parser.add_argument("--resource", default=defaults["usb_resource"], help="VISA resource")
    parser.add_argument("--visa-timeout-ms", type=int, default=defaults["timeout_ms"], help="VISA timeout in milliseconds")
    parser.add_argument("--persist", action=argparse.BooleanOptionalAction, default=defaults["persist"], help="copy waveform into persistent memory")
    parser.add_argument("--user-slot", type=int, choices=range(32), metavar="0..31", help="persistent memory slot")
    parser.add_argument("--channel", type=int, choices=(1, 2), default=defaults["channel"], help="AWG output channel")
    parser.add_argument("--dry-run", action="store_true", help="validate and encode without contacting the AWG")
    parser.add_argument("--enable-output", action=argparse.BooleanOptionalAction, default=defaults["enable_output"], help="leave selected channel enabled after import")
    parser.add_argument("--frequency", "--frequency-hz", dest="frequency_hz", type=float, default=defaults["frequency_hz"], help="override frequency in Hz")
    parser.add_argument("--amplitude", "--amplitude-vpp", dest="amplitude_vpp", type=float, default=defaults["voltage_vpp"], help="override amplitude in Vpp")
    parser.add_argument("--offset", "--offset-v", dest="offset_voltage", type=float, default=defaults["offset_voltage"], help="override offset in volts")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        defaults = load_defaults(args.defaults_file)
        config = Config.from_defaults({**defaults, "timeout_ms": args.visa_timeout_ms})
        control_mode = args.list_resources or args.idn is not None or args.output is not None
        if control_mode and args.waveform_file is not None:
            raise ValueError("Do not provide a waveform file with --list-resources, --idn, or --output")
        if args.list_resources:
            resources = list_resources()
            print(*resources, sep="\n") if resources else print("No VISA resources found", file=sys.stderr)
        if args.idn is not None:
            print(idn(args.idn, args.visa_timeout_ms))
            return 0
        if args.list_resources:
            return 0
        if args.output is not None:
            if not args.resource:
                raise ValueError("--resource is required for --output")
            set_output_state(args.resource, args.visa_timeout_ms, args.channel, args.output == "on")
            print(f"Channel {args.channel} output: {args.output.upper()}")
            return 0
        if args.waveform_file is None:
            raise ValueError("A waveform file is required")
        if args.visa_timeout_ms <= 0 or args.frequency_hz <= 0 or args.amplitude_vpp <= 0 or not math.isfinite(args.offset_voltage):
            raise ValueError("timeout, frequency, and amplitude must be positive; offset must be finite")
        if args.csv_value_column is not None and args.csv_value_column < 0:
            raise ValueError("--csv-column must be zero or greater")
        if args.user_slot is not None and not args.persist:
            raise ValueError("--user-slot requires --persist")
        waveform = load_waveform(args.waveform_file, config, csv_delimiter=args.csv_delimiter, csv_value_column=args.csv_value_column)
        payload = encode_dab(waveform, config)
        print(f"Name: {waveform.name}\nType: {waveform.waveform_type}\nPoints: {waveform.sample_count}\nPayload: {len(payload)} bytes\nIEEE header: {make_ieee_block(payload)[:6].decode('ascii')}\nChannel: {args.channel}\nAmplitude: {args.amplitude_vpp:g} Vpp\nOffset: {args.offset_voltage:g} V\nFrequency: {args.frequency_hz:g} Hz")
        if args.dry_run:
            print("Dry run complete; the instrument was not contacted")
            return 0
        raise RuntimeError("DG1022 waveform upload is not implemented yet; use --dry-run while SCPI upload commands are being validated")
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"Import failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
