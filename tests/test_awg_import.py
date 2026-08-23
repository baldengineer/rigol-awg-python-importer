from __future__ import annotations

from pathlib import Path

import awg_import
import pytest


class FakeConnection:
    instances: list["FakeConnection"] = []
    nonvolatile_free = "10"
    nonvolatile_catalog = '""'

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
        if command == "DATA:NVOL:FREE?":
            return self.nonvolatile_free
        if command == "DATA:NVOL:CAT?":
            return self.nonvolatile_catalog
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
        allow_channel_2=True,
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
    assert "DATA:COPY C" in writes
    assert "FUNC:USER:CH2 C" in writes
    assert "OUTP:CH2 OFF" in writes
    assert writes[-1] == "SYST:LOC"
    assert "FUNC:USER:CH2?" in FakeConnection.instances[0].queries
    assert result["selected_user_waveform"] == "C"
    assert result["user_memory"] == "C"


def test_debug_traces_scpi_commands_and_responses(monkeypatch, capsys) -> None:
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
    config = awg_import.Config(max_point_count=524_288, max_dac_code=16_383)

    awg_import.upload_waveform(
        "USB0::TEST::INSTR",
        5_000,
        waveform,
        user_slot=None,
        channel=2,
        allow_channel_2=True,
        enable_output=False,
        amplitude_vpp=2.0,
        offset_voltage=0.0,
        frequency_hz=1_000.0,
        config=config,
        persistent_name="C",
        debug=True,
    )

    debug = capsys.readouterr().err
    assert "SCPI >> FUNC:CH2 USER" in debug
    assert "SCPI >> DATA:DAC VOLATILE,0,8192,16383" in debug
    assert "SCPI >> FUNC:USER:CH2?" in debug
    assert 'SCPI << "CH2:ARB"' not in debug
    assert "SCPI << CH2:ARB" in debug


def test_channel_two_is_rejected_by_default() -> None:
    with pytest.raises(ValueError, match="allow-channel-2"):
        awg_import.reject_channel_2(2, False)


def test_channel_two_can_be_explicitly_allowed() -> None:
    awg_import.reject_channel_2(2, True)


def test_channel_one_is_allowed() -> None:
    awg_import.reject_channel_2(1, False)


def test_loads_current_arbdraw_example_and_uses_awg_playback_frequency() -> None:
    config = awg_import.Config(max_point_count=524_288)
    waveform = awg_import.load_arbdraw_json(
        Path(__file__).parents[1] / "examples" / "3_sine_2Mhz_run_at_6MHz.arbdraw.json",
        config,
    )

    assert waveform.name == "3_sine_2Mhz_run_at_6MHz"
    assert waveform.waveform_type == "sine"
    assert waveform.sample_count == 10_001
    assert waveform.sample_rate_sa == 1_250_000_000
    assert waveform.frequency_hz == pytest.approx(666_666.6666666666)


def test_arbdraw_sample_values_do_not_accept_numeric_strings() -> None:
    document = {
        "schema": "arbdraw.waveform",
        "version": 1,
        "waveform": {
            "sampleCount": "2",
            "sampleRateMSa": "1",
            "frequencyHz": "1",
            "lowVoltage": "-1",
            "highVoltage": "1",
            "values": ["-1", 1],
        },
    }

    with pytest.raises(ValueError, match="finite JSON numbers"):
        awg_import.waveform_from_document(document, awg_import.Config())


def test_upload_rejects_duplicate_before_data_transfer(monkeypatch) -> None:
    FakeConnection.instances.clear()
    monkeypatch.setattr(awg_import, "VisaConnection", FakeConnection)
    monkeypatch.setattr(awg_import.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(FakeConnection, "nonvolatile_free", "9")
    monkeypatch.setattr(FakeConnection, "nonvolatile_catalog", '"C"')
    waveform = awg_import.Waveform(
        name="test",
        waveform_type="custom",
        values=(-1.0, 0.0, 1.0),
        low_voltage=-1.0,
        high_voltage=1.0,
        sample_rate_sa=3_000.0,
        frequency_hz=1_000.0,
    )
    config = awg_import.Config(max_point_count=524_288, max_dac_code=16_383)

    with pytest.raises(RuntimeError, match="already exists"):
        awg_import.upload_waveform(
            "USB0::TEST::INSTR",
            5_000,
            waveform,
            user_slot=None,
            channel=2,
            allow_channel_2=True,
            enable_output=False,
            amplitude_vpp=2.0,
            offset_voltage=0.0,
            frequency_hz=1_000.0,
            config=config,
            persistent_name="C",
        )

    assert not any(command.startswith("DATA:DAC") for command in FakeConnection.instances[0].writes)


def test_overwrite_deletes_existing_name_before_copy(monkeypatch) -> None:
    FakeConnection.instances.clear()
    monkeypatch.setattr(awg_import, "VisaConnection", FakeConnection)
    monkeypatch.setattr(awg_import.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(FakeConnection, "nonvolatile_free", "9")
    monkeypatch.setattr(FakeConnection, "nonvolatile_catalog", '"C"')
    waveform = awg_import.Waveform(
        name="test",
        waveform_type="custom",
        values=(-1.0, 0.0, 1.0),
        low_voltage=-1.0,
        high_voltage=1.0,
        sample_rate_sa=3_000.0,
        frequency_hz=1_000.0,
    )
    config = awg_import.Config(max_point_count=524_288, max_dac_code=16_383)

    awg_import.upload_waveform(
        "USB0::TEST::INSTR",
        5_000,
        waveform,
        user_slot=None,
        channel=2,
        allow_channel_2=True,
        enable_output=False,
        amplitude_vpp=2.0,
        offset_voltage=0.0,
        frequency_hz=1_000.0,
        config=config,
        persistent_name="C",
        overwrite=True,
    )

    writes = FakeConnection.instances[0].writes
    assert "DATA:DEL C" in writes
    assert "DATA:COPY C" in writes


# SPDX-License-Identifier: MIT
# Copyright (c) 2026 James Lewis
