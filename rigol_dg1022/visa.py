"""Small, testable PyVISA wrapper for the DG1022 USB Device interface."""

from __future__ import annotations

from typing import Any


DEFAULT_TIMEOUT_MS = 5_000
TERMINATOR = "\n"


def _pyvisa() -> Any:
    try:
        import pyvisa
    except ImportError as exc:
        raise RuntimeError(
            "PyVISA is required; install requirements.txt and a VISA backend "
            "such as NI-VISA or Keysight IO Libraries"
        ) from exc
    return pyvisa


def _resource_manager() -> Any:
    """Open the system VISA backend, falling back to PyVISA-py when present."""
    pyvisa = _pyvisa()
    try:
        return pyvisa.ResourceManager()
    except Exception as native_error:
        try:
            return pyvisa.ResourceManager("@py")
        except Exception as python_error:
            raise RuntimeError(
                f"Could not open a VISA backend: {native_error}; PyVISA-py fallback: {python_error}"
            ) from python_error


class VisaConnection:
    """Open one VISA resource and expose newline-terminated SCPI operations."""

    def __init__(self, resource: str, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> None:
        if not isinstance(resource, str) or not resource.strip():
            raise ValueError("resource must be a non-empty VISA resource string")
        if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or timeout_ms <= 0:
            raise ValueError("timeout_ms must be a positive integer")
        self.resource_name = resource
        self.timeout_ms = timeout_ms
        self._manager: Any | None = None
        self._instrument: Any | None = None

    def __enter__(self) -> "VisaConnection":
        self._manager = _resource_manager()
        try:
            self._instrument = self._manager.open_resource(self.resource_name)
            self._instrument.timeout = self.timeout_ms
            self._instrument.write_termination = TERMINATOR
            self._instrument.read_termination = TERMINATOR
        except Exception:
            self.close()
            raise
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if self._instrument is not None:
            self._instrument.close()
            self._instrument = None
        if self._manager is not None:
            self._manager.close()
            self._manager = None

    @property
    def instrument(self) -> Any:
        if self._instrument is None:
            raise RuntimeError("VISA connection is not open")
        return self._instrument

    def write(self, command: str) -> None:
        self.instrument.write(command)

    def query(self, command: str) -> str:
        response = self.instrument.query(command).strip()
        if not response:
            raise RuntimeError(f"Instrument returned an empty response to {command!r}")
        return response


def list_resources() -> tuple[str, ...]:
    """Return resources reported by the installed VISA backend."""
    manager = _resource_manager()
    try:
        return tuple(manager.list_resources())
    finally:
        manager.close()


def idn(resource: str, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> str:
    """Connect to *resource* and return its IEEE-488.2 identity response."""
    with VisaConnection(resource, timeout_ms) as connection:
        return connection.query("*IDN?")
