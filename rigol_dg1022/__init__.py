"""Communication primitives for the Rigol DG1022."""

from .visa import VisaConnection, idn, list_resources
from .specs import AwgSpecs, ChannelSpecs, load_specs

__all__ = [
    "AwgSpecs",
    "ChannelSpecs",
    "VisaConnection",
    "idn",
    "list_resources",
    "load_specs",
]


# SPDX-License-Identifier: MIT
# Copyright (c) 2026 James Lewis
