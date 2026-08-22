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
import re
import struct
import sys
import time
import tomllib
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from rigol_dg1022.visa import DEFAULT_TIMEOUT_MS, VisaConnection, idn, list_resources
from rigol_dg1022.specs import load_specs


DEFAULTS_FILE = "defaults.toml"
SCPI_SETTLE_SECONDS = 2.0
DATA_DAC_SETTLE_SECONDS = 2.0


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


def encode_dac_values(waveform: Waveform, config: Config) -> tuple[int, ...]:
    """Map waveform voltages to the DG1000's 14-bit DAC values."""
    span = waveform.amplitude_vpp
    return tuple(
        int(
            max(0.0, min(1.0, (value - waveform.low_voltage) / span))
            * config.max_dac_code
            + 0.5
        )
        for value in waveform.values
    )


def make_dac_command(waveform: Waveform, config: Config) -> str:
    """Build the ASCII DATA:DAC command specified by the DG1000 manual."""
    values = ",".join(str(value) for value in encode_dac_values(waveform, config))
    return f"DATA:DAC VOLATILE,{values}"


def make_ieee_block(payload: bytes) -> bytes:
    length = str(len(payload)).encode("ascii")
    return b"#" + str(len(length)).encode("ascii") + length + payload


def set_output_state(resource: str, timeout_ms: int, channel: int, enabled: bool) -> None:
    output_command = "OUTP" if channel == 1 else "OUTP:CH2"
    with VisaConnection(resource, timeout_ms) as connection:
        connection.write(f"{output_command} {'ON' if enabled else 'OFF'}")


def _require_no_scpi_error(connection: VisaConnection, stage: str) -> str:
    status = connection.query("SYST:ERR?")
    match = re.match(r"^\s*([+-]?\d+)(?:\s*,|\s*$)", status)
    if match is None:
        raise RuntimeError(f"Unparseable SCPI error response after {stage}: {status}")
    if int(match.group(1)) != 0:
        raise RuntimeError(f"SCPI error after {stage}: {status}")
    return status


def _write_and_check(
    connection: VisaConnection,
    command: str,
    stage: str,
    *,
    settle_seconds: float = SCPI_SETTLE_SECONDS,
) -> str:
    connection.write(command)
    time.sleep(settle_seconds)
    return _require_no_scpi_error(connection, stage)


def _user_wave_name(user_slot: int) -> str:
    if not 0 <= user_slot <= 9:
        raise ValueError("--user-slot must be between 0 and 9 for the DG1000")
    return chr(ord("A") + user_slot)


def upload_waveform(
    resource: str,
    timeout_ms: int,
    waveform: Waveform,
    user_slot: int | None,
    channel: int,
    enable_output: bool,
    amplitude_vpp: float,
    offset_voltage: float,
    frequency_hz: float,
    config: Config,
) -> dict[str, str]:
    """Upload one ASCII DAC waveform and configure either DG1000 channel."""
    specs = load_specs()
    channel_specs = specs.channel(channel)
    if waveform.sample_count > channel_specs.arb_memory_depth_points:
        raise ValueError(
            f"waveform has {waveform.sample_count} points, but CH{channel} supports "
            f"{channel_specs.arb_memory_depth_points} arbitrary points"
        )
    output = "OUTP" if channel == 1 else "OUTP:CH2"
    frequency = "FREQ" if channel == 1 else "FREQ:CH2"
    unit = "VOLT:UNIT" if channel == 1 else "VOLT:UNIT:CH2"
    high = "VOLT:HIGH" if channel == 1 else "VOLT:HIGH:CH2"
    low = "VOLT:LOW" if channel == 1 else "VOLT:LOW:CH2"
    select = "FUNC:USER" if channel == 1 else "FUNC:USER:CH2"
    function_query = "FUNC?" if channel == 1 else "FUNC:CH2?"
    function = "FUNC" if channel == 1 else "FUNC:CH2"
    user_name = None if user_slot is None else _user_wave_name(user_slot)
    low_voltage = offset_voltage - amplitude_vpp / 2.0
    high_voltage = offset_voltage + amplitude_vpp / 2.0
    completed = False

    with VisaConnection(resource, timeout_ms) as connection:
        identity = connection.query("*IDN?")
        if config.expected_identity_prefix and not identity.startswith(config.expected_identity_prefix):
            raise RuntimeError(f"Unexpected instrument identity: {identity}")
        try:
            _write_and_check(connection, f"{output} OFF", f"disabling channel {channel}")
            _write_and_check(
                connection,
                f"{function} USER",
                f"selecting arbitrary mode on channel {channel}",
            )
            _write_and_check(
                connection,
                f"{frequency} {frequency_hz:.12g}",
                f"setting channel {channel} frequency",
            )
            _write_and_check(
                connection,
                f"{unit} VPP",
                f"setting channel {channel} voltage unit",
            )
            _write_and_check(
                connection,
                f"{high} {high_voltage:.12g}",
                f"setting channel {channel} high level",
            )
            _write_and_check(
                connection,
                f"{low} {low_voltage:.12g}",
                f"setting channel {channel} low level",
            )
            status = _write_and_check(
                connection,
                make_dac_command(waveform, config),
                "loading DATA:DAC volatile memory",
                settle_seconds=DATA_DAC_SETTLE_SECONDS,
            )
            if user_name is not None:
                _write_and_check(
                connection,
                f"DATA:COPY {user_name},VOLATILE",
                f"storing waveform as {user_name}",
                    settle_seconds=2.0,
                )
                selected_name = user_name
            else:
                selected_name = "VOLATILE"
            _write_and_check(
                connection,
                f"{select} {selected_name}",
                f"selecting {selected_name} on channel {channel}",
            )
            selected_function = connection.query(function_query)
            _write_and_check(
                connection,
                f"{output} {'ON' if enable_output else 'OFF'}",
                f"setting final channel {channel} output",
            )
            output_state = connection.query(f"{output}?").strip().upper()
            expected_output = "ON" if enable_output else "OFF"
            if output_state not in {expected_output, "1" if enable_output else "0"}:
                raise RuntimeError(
                    f"Channel {channel} output state is {output_state}; expected {expected_output}"
                )
            reported_points = connection.query(f"DATA:ATTR:POINTS? {selected_name}")
            completed = True
            return {
                "identity": identity,
                "points": str(waveform.sample_count),
                "reported_points": reported_points,
                "user_memory": user_name or "",
                "channel": str(channel),
                "function": selected_function,
                "output": output_state,
                "error": status,
            }
        finally:
            try:
                if not completed or not enable_output:
                    connection.write(f"{output} OFF")
                    time.sleep(SCPI_SETTLE_SECONDS)
            finally:
                # DG1000 documents SYSTem:LOCal as the command that returns
                # control to the front panel after remote operation.
                try:
                    connection.write("SYST:LOC")
                    time.sleep(SCPI_SETTLE_SECONDS)
                except RuntimeError:
                    # Do not mask the original upload error if the session is
                    # already unusable during cleanup.
                    pass


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
    parser.add_argument("--user-slot", type=int, choices=range(10), metavar="0..9", help="persistent DG1000 memory slot (0=A through 9=J)")
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
        channel_specs = load_specs().channel(args.channel)
        if waveform.sample_count > channel_specs.arb_memory_depth_points:
            raise ValueError(
                f"waveform has {waveform.sample_count} points, but CH{args.channel} "
                f"supports {channel_specs.arb_memory_depth_points} arbitrary points"
            )
        dac_command = make_dac_command(waveform, config)
        print(f"Name: {waveform.name}\nType: {waveform.waveform_type}\nPoints: {waveform.sample_count}\nChannel memory: {channel_specs.arb_memory_depth_points} points\nDATA:DAC payload: {len(dac_command.encode('ascii'))} ASCII bytes\nChannel: {args.channel}\nAmplitude: {args.amplitude_vpp:g} Vpp\nOffset: {args.offset_voltage:g} V\nFrequency: {args.frequency_hz:g} Hz")
        if args.dry_run:
            print("Dry run complete; the instrument was not contacted")
            return 0
        result = upload_waveform(
            args.resource,
            args.visa_timeout_ms,
            waveform,
            args.user_slot if args.persist else None,
            args.channel,
            args.enable_output,
            args.amplitude_vpp,
            args.offset_voltage,
            args.frequency_hz,
            config,
        )
        print(result["identity"])
        print(
            f"Loaded {result['points']} requested points into {result['function']} "
            f"on CH{result['channel']}"
        )
        if result["reported_points"] != result["points"]:
            print(f"Instrument-reported volatile memory: {result['reported_points']} points")
        if result["user_memory"]:
            print(f"Persistent memory: {result['user_memory']}")
        print(f"Channel {result['channel']} output: {result['output']}")
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"Import failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
