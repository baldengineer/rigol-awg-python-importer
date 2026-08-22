---
name: dg1022-arbitrary-waveforms
description: Work with Rigol DG1022 arbitrary waveforms over VISA/SCPI, including upload, channel selection, catalog queries, nonvolatile waveform names, size reporting, and rename operations.
---

# Rigol DG1022 arbitrary waveforms

Use this skill for changes or diagnostics involving arbitrary-waveform data on
the Rigol DG1022/DG1000 command set. Treat the observations below as
instrument-specific project knowledge. Distinguish confirmed hardware behavior
from programming-manual limits and unresolved questions.

## Connection and cleanup

- The instrument is accessed through a USBTMC VISA resource, commonly named
  like `USB0::0x1AB1::0x0588::<serial>::INSTR`.
- PyVISA with either a native VISA implementation or PyVISA-py works for the
  project transport. Commands and responses use newline termination.
- After a remote session, send `SYST:LOC` so the front panel returns to local
  control. Do this in cleanup without hiding the original operation error.
- A query timeout means the instrument did not return a response; it is not
  necessarily a SCPI syntax error. Reopen the VISA session before probing a
  different command.

## Waveform data model

- The programming guide documents `DATA:DAC VOLATILE,<value>,...` with integer
  DAC values from 0 through 16383. These are the 14-bit values used by the
  current importer.
- The guide also documents `DATA VOLATILE,<value>,...` for floating-point
  values from -1 through 1. The current upload path uses `DATA:DAC`.
- Uploading to `VOLATILE` replaces the previous volatile waveform.
- Select arbitrary mode before selecting the waveform:
  - CH1: `FUNC USER`, then `FUNC:USER <name>`.
  - CH2: `FUNC:CH2 USER`, then `FUNC:USER:CH2 <name>`.
- Verify selection with `FUNC:USER?` for CH1 or `FUNC:USER:CH2?` for CH2.
- Use channel-specific forms for frequency, voltage, and output commands:
  `FREQ`/`FREQ:CH2`, `VOLT:*`/`VOLT:*:CH2`, and `OUTP`/`OUTP:CH2`.

## Channel limits

The project’s hardware specification resource records the observed DG1022
limits:

| Channel | Arbitrary waveform depth |
| --- | ---: |
| CH1 | 4,096 points |
| CH2 | 1,024 points |

Both channels are recorded as 14-bit and 100 MSa/s in the project specs. The
programming guide states a protocol-level arbitrary-waveform range of 1 to
524,288 points, but the channel-specific hardware limits above are the limits
to enforce for this instrument.

## Catalog and nonvolatile waveform behavior

The DATA commands begin in the DG1000 programming guide around page 64 (guide
page 2-52). The exact catalog commands are described on guide page 2-55:

- `DATA:CAT?` returns all selectable names: the five built-ins, `VOLATILE`
  when present, and user-defined nonvolatile waveforms.
- The built-in names observed/documented are `EXP_RISE`, `EXP_FALL`,
  `NEG_RAMP`, `SINC`, and `CARDIAC`.
- `DATA:NVOL:FREE?` returns the number of unused nonvolatile waveform memories,
  from 0 through 10.
- `DATA:NVOL:CAT?` or the long form `DATA:NVOLatile:CATalog?` returns
  user-defined nonvolatile names when at least one exists.

Important DG1022 firmware quirks:

- When all ten nonvolatile waveform memories are empty,
  `DATA:NVOL:FREE?` returns `10`, but the NVOL catalog query returns no
  response and causes a VISA timeout. Always query `DATA:NVOL:FREE?` first;
  when it is 10, report that no saved user waveforms were found and do not
  issue the NVOL catalog query.
- With a saved waveform, the NVOL catalog can include a trailing comma, for
  example `"000",`. Accept and remove only that trailing separator.
- Waveform memories are addressed by names, not numeric slot IDs. The ten
  numbered `STATE1` through `STATE10` locations in the manual are separate
  instrument-state memories and must not be confused with DATA waveform
  memories.
- `DATA:COPY <destination arb name>,VOLATILE` copies the current volatile
  waveform into nonvolatile storage. Enforce the manual’s name rule for new
  names: 1-12 characters, first character A-Z or a-z, remaining characters
  A-Z/a-z, 0-9, or `_`, with no spaces.
- `DATA:RENAME <old name>,<new name>` renames a nonvolatile waveform. The
  validate the new name with the same creation rule. Existing legacy names
  such as `000` may still be accepted as the old reference so they can be
  migrated. The instrument may normalize the returned name to uppercase, so
  verify rename results case-insensitively.

## Stored size queries

- `DATA:ATTRibute:POINts? <name>` returns the point count for `VOLATILE` and
  saved custom waveforms. The project displays storage as two bytes per point,
  so 4,096 points is `8,192 bytes`.
- Do not issue the points query for built-in waveforms. On the DG1022 it
  returns `Invalid Command` and places an error in the SCPI error queue.
- Format custom entries as:

  ```text
  NAME: 4,096 Points (8,192 bytes)
  ```

## Error handling and verification

- Use `SYST:ERR?` after state-changing SCPI commands such as upload, copy,
  rename, mode selection, and output changes. Parse the signed numeric error
  code; `0` means no error.
- After upload, verify the channel function, selected user waveform, reported
  point count, and final output state. Keep output off unless the caller
  explicitly requests it.
- Do not treat the built-in `DATA:ATTR` failure as a general upload failure;
  it is an expected limitation for built-in names. Clear the resulting SCPI
  error before issuing more diagnostic queries.

## Useful project entry points

- `awg_import.py`: ArbDraw-compatible JSON/CSV upload, volatile upload, and
  optional `DATA:COPY` persistence.
- `data_catalog.py`: catalog listing, free-slot checks, point/byte reporting,
  and nonvolatile waveform rename.
- `rigol_dg1022/visa.py`: newline-terminated PyVISA transport and error
  wrapping.
- `rigol_dg1022/specs.toml`: channel-aware hardware limits.

When extending this skill, label new facts as hardware-tested, manual-only, or
unresolved. Do not generalize a DG1022 firmware quirk to unrelated Rigol
models without testing.
