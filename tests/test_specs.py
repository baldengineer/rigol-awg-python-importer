from rigol_dg1022.specs import load_specs


def test_packaged_specs_keep_channel_memory_depths_separate() -> None:
    specs = load_specs()

    assert specs.model == "DG1022"
    assert specs.channel(1).arb_memory_depth_points == 4096
    assert specs.channel(2).arb_memory_depth_points == 1024
    assert specs.channel(1).vertical_resolution_bits == 14
    assert specs.channel(2).sampling_rate_msps == 100.0
    assert specs.nonvolatile_waveform_count == 10
