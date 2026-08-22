from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from rigol_dg1022 import visa


class FakeInstrument:
    def __init__(self) -> None:
        self.timeout = None
        self.write_termination = None
        self.read_termination = None
        self.commands: list[str] = []
        self.closed = False

    def write(self, command: str) -> None:
        self.commands.append(command)

    def query(self, command: str) -> str:
        self.commands.append(command)
        return " RIGOL TECHNOLOGIES,DG1022,SN123,01.01\n"

    def close(self) -> None:
        self.closed = True


class FakeManager:
    def __init__(self, instrument: FakeInstrument) -> None:
        self.instrument = instrument
        self.closed = False

    def open_resource(self, _resource: str) -> FakeInstrument:
        return self.instrument

    def list_resources(self) -> tuple[str, ...]:
        return ("USB0::0x1AB1::0x0588::SN123::INSTR",)

    def close(self) -> None:
        self.closed = True


def test_idn_configures_and_queries_visa(monkeypatch: pytest.MonkeyPatch) -> None:
    instrument = FakeInstrument()
    manager = FakeManager(instrument)
    fake_pyvisa = SimpleNamespace(ResourceManager=lambda: manager)
    monkeypatch.setitem(sys.modules, "pyvisa", fake_pyvisa)

    assert visa.idn("USB0::TEST::INSTR", 1234) == "RIGOL TECHNOLOGIES,DG1022,SN123,01.01"
    assert instrument.timeout == 1234
    assert instrument.write_termination == "\n"
    assert instrument.read_termination == "\n"
    assert instrument.commands == ["*IDN?"]
    assert instrument.closed
    assert manager.closed


def test_invalid_timeout_is_rejected() -> None:
    with pytest.raises(ValueError, match="positive integer"):
        visa.VisaConnection("USB0::TEST::INSTR", 0)
