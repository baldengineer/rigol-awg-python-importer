# Example waveforms

These ArbDraw JSON and CSV files demonstrate waveform shapes and file sizes supported
by `awg_import.py`. They are intended for validation and instrument testing.

CSV input is headerless. Two-column files use `x,y`; single-column files contain
voltage samples only. Use `--csv-column` for a selected voltage column and
`--csv-delimiter` when the separator is not a comma.

| File | Shape | Points | Sample rate | Waveform frequency | Voltage range |
| --- | --- | ---: | ---: | ---: | ---: |
| `sample_waveform_01_funky_sine.json` | Custom/funky sine | 1,000 | 1,250 MSa/s | 2.5 MHz | -5 V to 5 V |
| `sample_waveform_100k_sine.arbdraw.json` | Sine (resampled) | 4,096 | 1,250 MSa/s | 1.25 GHz | -0.5 V to 0.5 V |
| `sample_waveform_17p-pulse.arbdraw.json` | Square/pulse | 1,000 | 1,250 MSa/s | 2.5 MHz | -0.5 V to 0.5 V |
| `uart_hello_115200.arbdraw.json` | Serial/UART pattern | 1,000 | 1,250 MSa/s | 2 kHz | -0.5 V to 0.5 V |
| `513pt-sine-wave.arbdraw.json` | Sine | 513 | 100 MSa/s | 2.345 MHz waveform / 1.1725 MHz AWG | -0.5 V to 0.5 V |
| `hello_world_56700.csv` | Headerless `x,y` CSV sine | 1,000 | 1,248.75 MSa/s | 1.24875 MHz | -0.5 V to 0.5 V |

The UART example contains a repeating waveform pattern intended to represent a
115200-baud `hello` transmission. Verify timing and polarity with an oscilloscope or
logic analyzer before connecting it to other equipment.

## Dry-run validation

From the project root, validate and preview an example without contacting the AWG:

```powershell
python .\awg_import.py .\examples\uart_hello_115200.arbdraw.json --dry-run
```

The importer reports the JSON sample rate separately and configures frequency from
`frequencyHz`, unless a value in `defaults.toml` or a command-line override takes
precedence.

All examples contain no more than 4,096 points, matching the DG1022 CH1 arbitrary
waveform memory limit. The two larger source waveforms have been resampled to fit.
