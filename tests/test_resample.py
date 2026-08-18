"""Unit tests for the analysis resampler (dashboard/main.py::_resample).

These were the ad-hoc checks run before shipping 1.3.0, promoted into the
repo so the behaviours they pin down survive future edits.
"""

import main


def test_linear_resample():
    dists = list(range(11))
    axis, out = main._resample(dists, {"v": [x * 2 for x in dists]}, 11)
    assert axis[0] == 0.0
    assert abs(axis[-1] - 10) < 1e-9
    assert all(abs(out["v"][i] - i * 2) < 1e-6 for i in range(11))


def test_parked_frames_no_nan():
    # Plateaued distance (car stationary) must interpolate flat, never divide
    # 0/0 into a NaN hole in the chart.
    axis, out = main._resample([0, 1, 1, 1, 2, 3], {"v": [0, 10, 20, 30, 40, 50]}, 20)
    assert len(out["v"]) == 20
    assert not any(v != v for v in out["v"])  # NaN != NaN


def test_offset_start_rebases_to_zero():
    axis, out = main._resample([500, 510, 520], {"v": [1, 2, 3]}, 5)
    assert abs(axis[0]) < 1e-9
    assert abs(axis[-1] - 20) < 1e-9


def test_degenerate_inputs():
    assert main._resample([], {"v": []}, 10) == ([], {"v": []})
    # All samples at the same distance collapses to a single point.
    axis, out = main._resample([5, 5, 5], {"v": [7, 7, 7]}, 10)
    assert axis == [0.0]
    assert out["v"] == [7]


def test_multi_channel_stays_aligned():
    axis, out = main._resample(
        [0, 2, 4, 6, 8],
        {"t": [0, 1, 2, 3, 4], "s": [100, 90, 80, 70, 60]},
        50,
    )
    assert all(out["t"][i] <= out["t"][i + 1] + 1e-9 for i in range(49))
    assert all(out["s"][i] >= out["s"][i + 1] - 1e-9 for i in range(49))


def test_session_id_validation():
    assert main._valid_sid("89cd047c")
    assert main._valid_sid("abc-DEF_123")
    assert not main._valid_sid("")
    assert not main._valid_sid('x" or true or "')   # Flux breakout attempt
    assert not main._valid_sid("a\\b")
    assert not main._valid_sid("x" * 65)
