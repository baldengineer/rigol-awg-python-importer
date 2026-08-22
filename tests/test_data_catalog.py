from __future__ import annotations

import data_catalog
import pytest


class FakeConnection:
    instances: list["FakeConnection"] = []

    def __init__(self, _resource: str, _timeout_ms: int) -> None:
        self.commands: list[str] = []
        self.instances.append(self)

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, *_args: object) -> None:
        pass

    def query(self, command: str) -> str:
        self.commands.append(command)
        if command == data_catalog.FREE_SLOTS_QUERY:
            return "9"
        if command == data_catalog.USER_CATALOG_QUERY:
            return '"A","my_waveform","J"'
        if command == data_catalog.FULL_CATALOG_QUERY:
            return '"VOLATILE","NEG_RAMP","A"'
        if command == "SYST:ERR?":
            return '+0,"No Error"'
        raise AssertionError(f"unexpected query: {command}")

    def write(self, command: str) -> None:
        self.commands.append(command)


def test_parse_catalog_response() -> None:
    assert data_catalog.parse_catalog_response('"A","B","C"') == ("A", "B", "C")
    assert data_catalog.parse_catalog_response('"A","B",') == ("A", "B")
    assert data_catalog.parse_catalog_response('""') == ()
    assert data_catalog.parse_free_slots_response("9") == 9


def test_query_user_memory_names_uses_nvolatile_catalog(monkeypatch) -> None:
    FakeConnection.instances.clear()
    monkeypatch.setattr(data_catalog, "VisaConnection", FakeConnection)

    assert data_catalog.query_user_memory_names("USB0::TEST::INSTR") == (
        "A",
        "my_waveform",
        "J",
    )
    assert FakeConnection.instances[0].commands == [
        data_catalog.FREE_SLOTS_QUERY,
        data_catalog.USER_CATALOG_QUERY,
        "SYST:LOC",
    ]


def test_empty_user_memory_skips_catalog_query(monkeypatch) -> None:
    FakeConnection.instances.clear()

    class EmptyConnection(FakeConnection):
        def query(self, command: str) -> str:
            self.commands.append(command)
            if command == data_catalog.FREE_SLOTS_QUERY:
                return "10"
            raise AssertionError(f"unexpected query after empty-memory check: {command}")

    monkeypatch.setattr(data_catalog, "VisaConnection", EmptyConnection)

    assert data_catalog.query_user_memory_names("USB0::TEST::INSTR") == ()
    assert EmptyConnection.instances[0].commands == [
        data_catalog.FREE_SLOTS_QUERY,
        "SYST:LOC",
    ]


def test_query_all_waveform_names_uses_full_catalog(monkeypatch) -> None:
    FakeConnection.instances.clear()
    monkeypatch.setattr(data_catalog, "VisaConnection", FakeConnection)

    assert data_catalog.query_all_waveform_names("USB0::TEST::INSTR") == (
        "VOLATILE",
        "NEG_RAMP",
        "A",
    )
    assert FakeConnection.instances[0].commands == [
        data_catalog.FULL_CATALOG_QUERY,
        "SYST:LOC",
    ]


def test_query_free_slots(monkeypatch) -> None:
    FakeConnection.instances.clear()
    monkeypatch.setattr(data_catalog, "VisaConnection", FakeConnection)

    assert data_catalog.query_free_slots("USB0::TEST::INSTR") == 9
    assert FakeConnection.instances[0].commands == [
        data_catalog.FREE_SLOTS_QUERY,
        "SYST:LOC",
    ]


def test_rename_waveform_verifies_catalog(monkeypatch) -> None:
    FakeConnection.instances.clear()

    class RenameConnection(FakeConnection):
        def __init__(self, resource: str, timeout_ms: int) -> None:
            super().__init__(resource, timeout_ms)
            self.renamed = False

        def query(self, command: str) -> str:
            if command == data_catalog.USER_CATALOG_QUERY and self.renamed:
                self.commands.append(command)
                return '"A","A_First","J"'
            return super().query(command)

        def write(self, command: str) -> None:
            super().write(command)
            if command == "DATA:RENAME 000,A_First":
                self.renamed = True

    monkeypatch.setattr(data_catalog, "VisaConnection", RenameConnection)
    monkeypatch.setattr(data_catalog.time, "sleep", lambda _seconds: None)

    names = data_catalog.rename_waveform("USB0::TEST::INSTR", "000", "A_First")

    assert names == ("A", "A_First", "J")
    assert FakeConnection.instances[0].commands == [
        "DATA:RENAME 000,A_First",
        "SYST:ERR?",
        data_catalog.USER_CATALOG_QUERY,
        "SYST:LOC",
    ]


def test_rename_rejects_invalid_new_waveform_name(monkeypatch) -> None:
    monkeypatch.setattr(data_catalog, "VisaConnection", FakeConnection)

    for invalid_name in ("0_First", "A name", "A-name", "ABCDEFGHIJKLM"):
        with pytest.raises(ValueError, match="start with a letter"):
            data_catalog.rename_waveform("USB0::TEST::INSTR", "000", invalid_name)


def test_query_waveform_byte_counts_handles_unavailable_builtin(monkeypatch) -> None:
    FakeConnection.instances.clear()

    class SizeConnection(FakeConnection):
        def query(self, command: str) -> str:
            self.commands.append(command)
            if command == f'{data_catalog.POINTS_QUERY} EXP_RISE':
                return "Invalid Command"
            if command == "SYST:ERR?":
                return '-118,"Invalid parameter"'
            if command == f'{data_catalog.POINTS_QUERY} 0_FIRST':
                return "4096"
            raise AssertionError(f"unexpected query: {command}")

    monkeypatch.setattr(data_catalog, "VisaConnection", SizeConnection)

    assert data_catalog.query_waveform_byte_counts(
        "USB0::TEST::INSTR",
        ("EXP_RISE", "0_FIRST"),
    ) == {"EXP_RISE": None, "0_FIRST": 8192}
    assert SizeConnection.instances[0].commands == [
        f'{data_catalog.POINTS_QUERY} 0_FIRST',
        "SYST:LOC",
    ]


def test_print_full_catalog_groups_built_ins_and_custom(capsys) -> None:
    data_catalog.print_full_catalog(
        ("VOLATILE", "NEG_RAMP", "000", "SINC"),
        9,
        {"VOLATILE": 8192, "NEG_RAMP": None, "000": 8192, "SINC": None},
    )

    assert capsys.readouterr().out == (
        "Free nonvolatile slots: 9\n"
        "\n"
        "Built-In:\n"
        "NEG_RAMP\n"
        "SINC\n"
        "\n"
        "Custom:\n"
        "VOLATILE: 4,096 Points (8,192 bytes)\n"
        "000: 4,096 Points (8,192 bytes)\n"
    )


# SPDX-License-Identifier: MIT
# Copyright (c) 2026 James Lewis
