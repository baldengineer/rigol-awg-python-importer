"""Packaged, channel-aware hardware specifications for the DG1022."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any


SPECS_FILE = "specs.toml"


@dataclass(frozen=True)
class ChannelSpecs:
    number: int
    arb_memory_depth_points: int
    vertical_resolution_bits: int
    vertical_resolution_includes_sign: bool
    sampling_rate_msps: float
    minimum_rise_fall_time_ns: float
    rise_fall_time_typical: bool
    jitter_rms_ns: float
    jitter_rms_period_ppm: float


@dataclass(frozen=True)
class AwgSpecs:
    manufacturer: str
    series: str
    model: str
    protocol_max_waveform_points: int
    nonvolatile_waveform_count: int
    nonvolatile_storage_scope: str
    channels: tuple[ChannelSpecs, ...]

    def channel(self, number: int) -> ChannelSpecs:
        for channel in self.channels:
            if channel.number == number:
                return channel
        raise ValueError(f"No specification is available for channel {number}")


def packaged_specs_resource() -> Any:
    return files("rigol_dg1022").joinpath(SPECS_FILE)


def _required_int(values: dict[str, Any], name: str, *, minimum: int = 0) -> int:
    value = values.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer of at least {minimum}")
    return value


def _required_float(values: dict[str, Any], name: str, *, positive: bool = False) -> float:
    value = values.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    result = float(value)
    if positive and result <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return result


def _required_bool(values: dict[str, Any], name: str) -> bool:
    value = values.get(name)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _load_table(path: str | Path | Any) -> dict[str, Any]:
    try:
        if isinstance(path, (str, Path)) and Path(path).name == SPECS_FILE and not Path(path).exists():
            path = packaged_specs_resource()
        if hasattr(path, "read_text"):
            values = tomllib.loads(path.read_text(encoding="utf-8"))
        else:
            with Path(path).open("rb") as stream:
                values = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"Could not read AWG specs TOML: {exc}") from exc
    if not isinstance(values, dict):
        raise ValueError("AWG specs TOML must contain a table")
    return values


def load_specs(path: str | Path | Any = SPECS_FILE) -> AwgSpecs:
    """Load and validate packaged or user-supplied AWG specifications."""
    values = _load_table(path)
    manufacturer = values.get("manufacturer")
    series = values.get("series")
    model = values.get("model")
    scope = values.get("nonvolatile_storage_scope")
    if not all(isinstance(value, str) and value for value in (manufacturer, series, model, scope)):
        raise ValueError("manufacturer, series, model, and nonvolatile_storage_scope must be non-empty strings")
    channels_table = values.get("channels")
    if not isinstance(channels_table, list) or not channels_table:
        raise ValueError("channels must be a non-empty TOML array of tables")

    channels: list[ChannelSpecs] = []
    for index, channel_values in enumerate(channels_table, start=1):
        if not isinstance(channel_values, dict):
            raise ValueError(f"channels[{index}] must be a table")
        channel = ChannelSpecs(
            number=_required_int(channel_values, "number", minimum=1),
            arb_memory_depth_points=_required_int(channel_values, "arb_memory_depth_points", minimum=2),
            vertical_resolution_bits=_required_int(channel_values, "vertical_resolution_bits", minimum=1),
            vertical_resolution_includes_sign=_required_bool(channel_values, "vertical_resolution_includes_sign"),
            sampling_rate_msps=_required_float(channel_values, "sampling_rate_msps", positive=True),
            minimum_rise_fall_time_ns=_required_float(channel_values, "minimum_rise_fall_time_ns", positive=True),
            rise_fall_time_typical=_required_bool(channel_values, "rise_fall_time_typical"),
            jitter_rms_ns=_required_float(channel_values, "jitter_rms_ns", positive=True),
            jitter_rms_period_ppm=_required_float(channel_values, "jitter_rms_period_ppm", positive=True),
        )
        if any(existing.number == channel.number for existing in channels):
            raise ValueError(f"duplicate channel number {channel.number}")
        channels.append(channel)

    protocol_limit = _required_int(values, "protocol_max_waveform_points", minimum=2)
    if any(channel.arb_memory_depth_points > protocol_limit for channel in channels):
        raise ValueError("channel memory depth cannot exceed protocol_max_waveform_points")
    return AwgSpecs(
        manufacturer=manufacturer,
        series=series,
        model=model,
        protocol_max_waveform_points=protocol_limit,
        nonvolatile_waveform_count=_required_int(values, "nonvolatile_waveform_count", minimum=1),
        nonvolatile_storage_scope=scope,
        channels=tuple(channels),
    )
