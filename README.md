# Rigol DG1022 ArbDraw Adapter

Initial communication layer for an ArbDraw adapter targeting the Rigol DG1022.
The DG1022 is controlled from a computer through its USB Device port using VISA
and newline-terminated SCPI commands.

## Setup

Create a virtual environment and install the Python dependency:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

PyVISA also needs a VISA implementation on the host, such as NI-VISA or Keysight
IO Libraries. Connect the instrument's USB Device port, then discover resources:

```powershell
python .\tools\rigol_idn.py --list-resources
```

Query the generator by passing the returned resource string:

```powershell
python .\tools\rigol_idn.py --resource "USB0::0x1AB1::...::INSTR"
```

The command sends `*IDN?` and prints the response. The resource is intentionally
not hard-coded because USB serial numbers and VISA backend naming vary by unit.

The ArbDraw-compatible importer has the same command-line options as the OWON
reference tool:

```powershell
python .\awg_import.py --help
python .\awg_import.py .\examples\sample_waveform_17p-pulse.arbdraw.json --dry-run
```

Resource listing, identity queries, and output control use the shared VISA
transport. The dry-run validates and encodes an input without contacting the
generator. Actual DG1022 waveform upload is deliberately gated until the
model-specific binary SCPI command sequence is verified on the connected unit.

## Scope of this first step

This commit establishes resource discovery, connection setup, SCPI write/query
handling, timeout management, and identity verification. ArbDraw waveform encoding
and DG1022-specific arbitrary-waveform commands will build on this transport after
the connected unit's identity and firmware behavior are confirmed.
