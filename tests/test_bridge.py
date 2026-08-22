from __future__ import annotations

from rigol_dg1022 import bridge


def _project() -> dict:
    return {
        "schema": "arbdraw.waveform",
        "version": 1,
        "name": "Bridge test",
        "waveform": {
            "type": "custom",
            "highVoltage": 1.0,
            "lowVoltage": -1.0,
            "sampleRateMSa": 1.0,
            "frequencyHz": 1000.0,
            "sampleCount": 4,
            "values": [0.0, 1.0, 0.0, -1.0],
        },
    }


def test_bridge_returns_json_safe_result(monkeypatch) -> None:
    def fake_upload(*_args, **_kwargs):
        return {
            "identity": "RIGOL TECHNOLOGIES,DG1022,SN,FW",
            "points": "4",
            "reported_points": "4096",
            "user_memory": "",
            "channel": "1",
            "function": "CH1:ARB",
            "selected_user_waveform": "VOLATILE",
            "output": "OFF",
            "error": '+0,"No Error"',
        }

    monkeypatch.setattr(bridge.awg_import, "upload_waveform", fake_upload)
    result = bridge.send_waveform(
        {
            "resource": "USB0::TEST::INSTR",
            "waveform": _project(),
            "options": {"channel": 1, "enable_output": False},
        }
    )

    assert result == {
        "status": "sent",
        "message": "Loaded 4 points into CH1:ARB on CH1; output is off.",
        "adapter": "rigol-dg1022",
        "identity": "RIGOL TECHNOLOGIES,DG1022,SN,FW",
        "points": 4,
        "instrument_reported_points": 4096,
        "channel": 1,
        "selected_user_waveform": "VOLATILE",
        "output_enabled": False,
        "persistent_memory": None,
    }


def test_bridge_rejects_non_usb_before_upload(monkeypatch) -> None:
    called = False

    def fake_upload(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("upload should not be reached")

    monkeypatch.setattr(bridge.awg_import, "upload_waveform", fake_upload)
    try:
        bridge.send_waveform(
            {"resource": "TCPIP0::192.0.2.1::INSTR", "waveform": _project()}
        )
    except ValueError as exc:
        assert "USBTMC" in str(exc)
    else:
        raise AssertionError("non-USB resource was accepted")
    assert not called


def test_bridge_rejects_unknown_option() -> None:
    try:
        bridge.send_waveform(
            {
                "resource": "USB0::TEST::INSTR",
                "waveform": _project(),
                "options": {"frequncy_hz": 1000},
            }
        )
    except ValueError as exc:
        assert "unknown option" in str(exc)
    else:
        raise AssertionError("unknown option was accepted")


# SPDX-License-Identifier: MIT
# Copyright (c) 2026 James Lewis
