"""Native ArbDraw bridge adapter for the Rigol DG1022."""

from __future__ import annotations

import math
import importlib.util
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .specs import load_specs

def _load_rigol_core() -> Any:
    """Load this adapter's CLI/library module without colliding with other adapters."""
    existing = sys.modules.get("awg_import")
    if existing is not None and hasattr(existing, "make_dac_command"):
        return existing
    core_path = Path(__file__).resolve().parents[1] / "awg_import.py"
    spec = importlib.util.spec_from_file_location("rigol_dg1022._awg_import_core", core_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load Rigol importer core from {core_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


awg_import = _load_rigol_core()


_RESOURCE_LOCKS: dict[str, threading.Lock] = {}
_RESOURCE_LOCKS_GUARD = threading.Lock()


def _lock_for(resource: str) -> threading.Lock:
    with _RESOURCE_LOCKS_GUARD:
        return _RESOURCE_LOCKS.setdefault(resource, threading.Lock())


def _number(value: Any, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"options.{name} must be a number")
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0):
        adjective = "positive finite" if positive else "finite"
        raise ValueError(f"options.{name} must be a {adjective} number")
    return result


@dataclass(frozen=True)
class AdapterOptions:
    channel: int = 1
    persist: bool = False
    user_slot: int | None = None
    enable_output: bool = False
    timeout_ms: int = 60000
    frequency_hz: float | None = None
    amplitude_vpp: float | None = None
    offset_voltage: float | None = None

    @classmethod
    def from_mapping(cls, values: dict[str, Any]) -> "AdapterOptions":
        allowed = {
            "channel",
            "persist",
            "user_slot",
            "enable_output",
            "timeout_ms",
            "frequency_hz",
            "amplitude_vpp",
            "offset_voltage",
        }
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ValueError(f"unknown option: {unknown[0]}")

        channel = values.get("channel", 1)
        if isinstance(channel, bool) or channel not in (1, 2):
            raise ValueError("options.channel must be 1 or 2")
        persist = values.get("persist", False)
        enable_output = values.get("enable_output", False)
        if not isinstance(persist, bool) or not isinstance(enable_output, bool):
            raise ValueError("options.persist and options.enable_output must be boolean")

        user_slot = values.get("user_slot")
        if user_slot is not None and (
            isinstance(user_slot, bool) or not isinstance(user_slot, int) or not 0 <= user_slot <= 9
        ):
            raise ValueError("options.user_slot must be an integer from 0 through 9 or null")
        if user_slot is not None and not persist:
            raise ValueError("options.user_slot requires options.persist=true")

        timeout_ms = values.get("timeout_ms", 60000)
        if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or timeout_ms <= 0:
            raise ValueError("options.timeout_ms must be a positive integer")

        def optional_number(name: str, *, positive: bool = False) -> float | None:
            value = values.get(name)
            return None if value is None else _number(value, name, positive=positive)

        return cls(
            channel=channel,
            persist=persist,
            user_slot=user_slot,
            enable_output=enable_output,
            timeout_ms=timeout_ms,
            frequency_hz=optional_number("frequency_hz", positive=True),
            amplitude_vpp=optional_number("amplitude_vpp", positive=True),
            offset_voltage=optional_number("offset_voltage"),
        )


def _require_usb_resource(resource: str) -> None:
    if not resource.upper().startswith("USB"):
        raise ValueError(
            "Rigol DG1022 waveform uploads require a USBTMC VISA resource; "
            "rejecting the request before opening the instrument"
        )


def send_waveform(request: dict[str, Any]) -> dict[str, Any]:
    """Validate and send one complete ArbDraw waveform document."""
    if not isinstance(request, dict):
        raise ValueError("request must be an object")
    resource = request.get("resource")
    if not isinstance(resource, str) or not resource.strip():
        raise ValueError("resource must be a non-empty VISA resource string")
    _require_usb_resource(resource)

    project = request.get("waveform")
    if not isinstance(project, dict):
        raise ValueError("waveform must be an object")
    options = request.get("options", {})
    if not isinstance(options, dict):
        raise ValueError("options must be an object")
    adapter_options = AdapterOptions.from_mapping(options)

    defaults = awg_import.load_defaults(awg_import.packaged_defaults_resource())
    config = awg_import.Config.from_defaults(
        {**defaults, "timeout_ms": adapter_options.timeout_ms}
    )
    waveform = awg_import.waveform_from_document(project, config)
    channel_specs = load_specs().channel(adapter_options.channel)
    if waveform.sample_count > channel_specs.arb_memory_depth_points:
        raise ValueError(
            f"waveform has {waveform.sample_count} points, but CH{adapter_options.channel} "
            f"supports {channel_specs.arb_memory_depth_points} arbitrary points"
        )

    frequency_hz = (
        waveform.frequency_hz
        if adapter_options.frequency_hz is None
        else adapter_options.frequency_hz
    )
    amplitude_vpp = (
        waveform.amplitude_vpp
        if adapter_options.amplitude_vpp is None
        else adapter_options.amplitude_vpp
    )
    offset_voltage = (
        waveform.offset_voltage
        if adapter_options.offset_voltage is None
        else adapter_options.offset_voltage
    )
    # Build the complete textual transfer before opening VISA.
    awg_import.make_dac_command(waveform, config)

    with _lock_for(resource):
        result = awg_import.upload_waveform(
            resource,
            adapter_options.timeout_ms,
            waveform,
            adapter_options.user_slot if adapter_options.persist else None,
            adapter_options.channel,
            adapter_options.enable_output,
            amplitude_vpp,
            offset_voltage,
            frequency_hz,
            config,
        )

    output_enabled = result["output"].upper() in {"ON", "1"}
    persistent_memory = result["user_memory"] or None
    return {
        "status": "sent",
        "message": (
            f"Loaded {waveform.sample_count} points into {result['function']} "
            f"on CH{adapter_options.channel}; output is "
            f"{'on' if output_enabled else 'off'}."
        ),
        "adapter": "rigol-dg1022",
        "identity": result["identity"],
        "points": waveform.sample_count,
        "instrument_reported_points": int(result["reported_points"]),
        "channel": adapter_options.channel,
        "output_enabled": output_enabled,
        "persistent_memory": persistent_memory,
    }
