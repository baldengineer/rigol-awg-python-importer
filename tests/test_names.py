from __future__ import annotations

import pytest

from rigol_dg1022.names import validate_waveform_name, validate_waveform_reference


@pytest.mark.parametrize("name", ("A", "A1", "A_First", "ABCDEFGHIJKL"))
def test_validate_waveform_name_accepts_manual_names(name: str) -> None:
    assert validate_waveform_name(name) == name


@pytest.mark.parametrize("name", ("", "0_First", "A name", "A-name", "ABCDEFGHIJKLM"))
def test_validate_waveform_name_rejects_invalid_names(name: str) -> None:
    with pytest.raises(ValueError, match="start with a letter"):
        validate_waveform_name(name)


def test_validate_waveform_reference_allows_legacy_digit_leading_name() -> None:
    assert validate_waveform_reference("000") == "000"


# SPDX-License-Identifier: MIT
# Copyright (c) 2026 James Lewis
