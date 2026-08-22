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
generator. Waveform upload uses the DG1000 programming-guide sequence: samples
are quantized to 14-bit values and sent as the ASCII `DATA:DAC VOLATILE,...`
command, then selected with `FUNC:USER VOLATILE` or `FUNC:USER:CH2 VOLATILE`.
Persistent uploads use `DATA:COPY` into one of the ten named user slots (`A`
through `J`). The channel-specific `FREQ`, `VOLT`, `FUNC`, and `OUTP` command
forms are used for CH1 and CH2 respectively. Keep `--enable-output` off while
validating a first hardware upload.

## Hardware specifications

Channel-aware specifications are stored in
[`rigol_dg1022/specs.toml`](rigol_dg1022/specs.toml) and loaded through
`rigol_dg1022.specs`. The DG1022 resource records the different arbitrary-memory
depths: 4096 points for CH1 and 1024 points for CH2. The importer checks the
selected channel's depth before a dry-run or hardware upload. The protocol-level
maximum from the programming manual remains recorded separately from the
channel-specific hardware limits.

## Links

- Product Page: [Rigol DG1000](https://www.rigolna.com/products/waveform-generators/dg1000/)

## Scope of this first step

This commit establishes resource discovery, connection setup, SCPI write/query
handling, timeout management, and identity verification. ArbDraw waveform encoding
and DG1022-specific arbitrary-waveform commands will build on this transport after
the connected unit's identity and firmware behavior are confirmed.
