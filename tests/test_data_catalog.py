from __future__ import annotations

import data_catalog


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


def test_print_full_catalog_groups_built_ins_and_custom(capsys) -> None:
    data_catalog.print_full_catalog(("VOLATILE", "NEG_RAMP", "000", "SINC"), 9)

    assert capsys.readouterr().out == (
        "Free nonvolatile slots: 9\n"
        "\n"
        "Built-In:\n"
        "NEG_RAMP\n"
        "SINC\n"
        "\n"
        "Custom:\n"
        "VOLATILE\n"
        "000\n"
    )


# SPDX-License-Identifier: MIT
# Copyright (c) 2026 James Lewis
