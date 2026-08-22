from __future__ import annotations

import awg_import


class FakeConnection:
    instances: list["FakeConnection"] = []

    def __init__(self, _resource: str, _timeout_ms: int) -> None:
        self.writes: list[str] = []
        self.queries: list[str] = []
        self.instances.append(self)

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, *_args: object) -> None:
        pass

    def write(self, command: str) -> None:
        self.writes.append(command)

    def query(self, command: str) -> str:
        self.queries.append(command)
        if command == "*IDN?":
            return "RIGOL TECHNOLOGIES,DG1022,SN123,01.01"
        if command == "FUNC:CH2?":
            return "CH2:ARB"
        if command == "FUNC:USER:CH2?":
            return "C"
        if command == "OUTP:CH2?":
            return "OFF"
        if command == "DATA:ATTR:POINTS? C":
            return "3"
        return '+0,"No Error"'


def test_upload_uses_documented_ch2_commands(monkeypatch) -> None:
    FakeConnection.instances.clear()
    monkeypatch.setattr(awg_import, "VisaConnection", FakeConnection)
    monkeypatch.setattr(awg_import.time, "sleep", lambda _seconds: None)
    waveform = awg_import.Waveform(
        name="test",
        waveform_type="custom",
        values=(-1.0, 0.0, 1.0),
        low_voltage=-1.0,
        high_voltage=1.0,
        sample_rate_sa=3_000.0,
        frequency_hz=1_000.0,
    )
    config = awg_import.Config(
        expected_identity_prefix="RIGOL TECHNOLOGIES,DG1022",
        max_point_count=524_288,
        max_dac_code=16_383,
        timeout_ms=5_000,
    )

    result = awg_import.upload_waveform(
        "USB0::TEST::INSTR",
        5_000,
        waveform,
        user_slot=2,
        channel=2,
        enable_output=False,
        amplitude_vpp=2.0,
        offset_voltage=0.0,
        frequency_hz=1_000.0,
        config=config,
    )

    writes = FakeConnection.instances[0].writes
    assert "FUNC:CH2 USER" in writes
    assert "FREQ:CH2 1000" in writes
    assert "VOLT:UNIT:CH2 VPP" in writes
    assert "VOLT:HIGH:CH2 1" in writes
    assert "VOLT:LOW:CH2 -1" in writes
    assert "DATA:DAC VOLATILE,0,8192,16383" in writes
    assert "DATA:COPY C,VOLATILE" in writes
    assert "FUNC:USER:CH2 C" in writes
    assert "OUTP:CH2 OFF" in writes
    assert writes[-1] == "SYST:LOC"
    assert "FUNC:USER:CH2?" in FakeConnection.instances[0].queries
    assert result["selected_user_waveform"] == "C"
    assert result["user_memory"] == "C"


# SPDX-License-Identifier: MIT
# Copyright (c) 2026 James Lewis
