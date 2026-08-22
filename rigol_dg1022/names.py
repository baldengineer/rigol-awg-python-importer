"""Validation helpers for DG1000 arbitrary-waveform names."""

from __future__ import annotations

import re


_WAVEFORM_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,11}$")
_WAVEFORM_REFERENCE = re.compile(r"^[A-Za-z0-9_]{1,12}$")


def validate_waveform_name(name: str, field: str = "waveform name") -> str:
    """Validate a new DG1000 waveform name using the programming manual rules."""
    if not isinstance(name, str) or _WAVEFORM_NAME.fullmatch(name) is None:
        raise ValueError(
            f"{field} must be 1-12 characters, start with a letter, and contain "
            "only letters, numbers, or underscores"
        )
    return name


def validate_waveform_reference(name: str, field: str = "waveform name") -> str:
    """Validate an existing waveform name used as a SCPI reference.

    Existing DG1022 firmware can contain legacy names that violate the
    letter-first creation rule, so references allow a leading digit while
    still rejecting whitespace and SCPI separators.
    """
    if not isinstance(name, str) or _WAVEFORM_REFERENCE.fullmatch(name) is None:
        raise ValueError(
            f"{field} must be 1-12 characters containing only letters, numbers, "
            "or underscores"
        )
    return name


# SPDX-License-Identifier: MIT
# Copyright (c) 2026 James Lewis
