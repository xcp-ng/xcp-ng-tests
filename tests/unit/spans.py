from __future__ import annotations

import pytest

from lib.common import GiB, KiB, TiB
from tests.storage.storage import compute_span_layout

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def spans_cover_device(layout: list[tuple[int, int]], dev_size: int) -> bool:
    """Return True if the spans cover [0, dev_size) with no gaps."""
    cursor = 0
    for position, size in layout:
        if position != cursor:
            return False
        cursor += size
    return cursor == dev_size


def no_overlaps(layout: list[tuple[int, int]]) -> bool:
    prev_end = 0
    for i, (position, size) in enumerate(layout):
        if i > 0 and position < prev_end:
            return False
        prev_end = position + size
    return True


# ---------------------------------------------------------------------------
# Basic structural guarantees
# ---------------------------------------------------------------------------

def test_first_span_starts_at_zero() -> None:
    layout = compute_span_layout(dev_size=100, total_size=90, num_spans=3, align=1)
    assert layout[0][0] == 0


def test_last_span_ends_at_dev_size() -> None:
    layout = compute_span_layout(dev_size=100, total_size=90, num_spans=3, align=1)
    position, size = layout[-1]
    assert position + size == 100


def test_no_overlaps_no_alignment() -> None:
    layout = compute_span_layout(dev_size=1000, total_size=500, num_spans=5, align=1)
    assert no_overlaps(layout)


def test_no_overlaps_with_alignment() -> None:
    layout = compute_span_layout(dev_size=1000, total_size=500, num_spans=5, align=64)
    assert no_overlaps(layout)


def test_correct_number_of_spans() -> None:
    for n in (1, 2, 3, 4, 5):
        layout = compute_span_layout(dev_size=1000, total_size=1000, num_spans=n, align=1)
        assert len(layout) == n


# ---------------------------------------------------------------------------
# num_spans=1
# ---------------------------------------------------------------------------

def test_single_span_covers_full_device() -> None:
    layout = compute_span_layout(dev_size=512, total_size=512, num_spans=1, align=1)
    assert layout == [(0, 512)]


def test_single_span_with_alignment() -> None:
    layout = compute_span_layout(dev_size=512, total_size=512, num_spans=1, align=512)
    assert layout == [(0, 512)]


# ---------------------------------------------------------------------------
# num_spans=2
# ---------------------------------------------------------------------------

def test_two_spans_full_coverage_no_alignment() -> None:
    # total_size=100=dev_size: positions at 0 and 50, sizes 50+50=100
    layout = compute_span_layout(dev_size=100, total_size=100, num_spans=2, align=1)
    assert layout[0] == (0, 50)
    assert layout[1] == (50, 50)


def test_two_spans_partial_no_alignment() -> None:
    # total_size=60: last span anchored at end of device, first span writes
    # its budget from position 0; gap between them
    layout = compute_span_layout(dev_size=100, total_size=60, num_spans=2, align=1)
    assert layout[0] == (0, 30)
    assert layout[1] == (70, 30)


def test_two_spans_aligned_position() -> None:
    # total_size=100, position[1] = 100*1//2 = 50, aligned down to 0 (50 % 64 = 50 → (50//64)*64=0)
    # Actually 50 // 64 = 0, so position[1] = 0... that would overlap.
    # Use align=32: position[1] = (50//32)*32 = 32
    layout = compute_span_layout(dev_size=100, total_size=100, num_spans=2, align=32)
    assert layout[0][0] == 0
    assert layout[1][0] % 32 == 0
    pos, size = layout[1]
    assert pos + size == 100


# ---------------------------------------------------------------------------
# num_spans=3, no alignment, full coverage
# ---------------------------------------------------------------------------

def test_three_spans_no_alignment_full_coverage_divisible() -> None:
    # dev_size divisible by num_spans: even split
    layout = compute_span_layout(dev_size=90, total_size=90, num_spans=3, align=1)
    assert layout == [(0, 30), (30, 30), (60, 30)]


def test_three_spans_no_alignment_full_coverage_indivisible() -> None:
    # dev_size=100 not divisible by 3: positions at 0, 33, 66 — no gap
    layout = compute_span_layout(dev_size=100, total_size=100, num_spans=3, align=1)
    positions = [p for p, _ in layout]
    assert positions == [0, 33, 66]
    assert spans_cover_device(layout, 100)


def test_three_spans_no_alignment_partial() -> None:
    # total_size=90 < dev_size=100: spans are spread across the device with
    # gaps between them. Each span writes total_size//num_spans = 30 bytes.
    # The last span is anchored at the end of the device.
    layout = compute_span_layout(dev_size=100, total_size=90, num_spans=3, align=1)
    assert layout[0] == (0, 30)
    assert layout[1] == (40, 30)
    pos, size = layout[2]
    assert pos + size == 100


# ---------------------------------------------------------------------------
# Full device coverage with alignment
# ---------------------------------------------------------------------------

def test_full_coverage_three_spans_aligned() -> None:
    layout = compute_span_layout(dev_size=100, total_size=100, num_spans=3, align=7)
    assert spans_cover_device(layout, 100)


def test_full_coverage_two_spans_aligned() -> None:
    layout = compute_span_layout(dev_size=100, total_size=100, num_spans=2, align=7)
    assert spans_cover_device(layout, 100)


def test_full_coverage_four_spans_aligned() -> None:
    layout = compute_span_layout(dev_size=1000, total_size=1000, num_spans=4, align=64)
    assert spans_cover_device(layout, 1000)


def test_full_coverage_large_alignment() -> None:
    # align=512, dev_size not a multiple of 512
    layout = compute_span_layout(dev_size=10007, total_size=10007, num_spans=3, align=512)
    assert spans_cover_device(layout, 10007)


# ---------------------------------------------------------------------------
# Carry logic: trimmed bytes propagate forward
# ---------------------------------------------------------------------------

def test_carry_prevents_gap_before_last_span() -> None:
    # dev_size=100, align=7, num_spans=3, total_size=100
    # Without carry: alignment trimming of early spans could leave a gap
    # before the last span. With carry the trimmed bytes are passed forward.
    layout = compute_span_layout(dev_size=100, total_size=100, num_spans=3, align=7)
    assert spans_cover_device(layout, 100)


def test_carry_accumulates_across_multiple_spans() -> None:
    # total_size=dev_size=1000, num_spans=5, align=64:
    # positions (align=64): 0, 192, 384, 576, 768
    # per_span=200; span[0] trimmed to 192 (+carry 8), span[1] gets 208 but
    # slot=192 so trimmed again (+carry 16), etc. Carry accumulates and ensures
    # full coverage with no gap before the last span.
    layout = compute_span_layout(dev_size=1000, total_size=1000, num_spans=5, align=64)
    assert spans_cover_device(layout, 1000)


def test_carry_no_effect_when_not_needed() -> None:
    # Perfectly divisible, align=1: no trimming, no carry, all spans equal size
    layout = compute_span_layout(dev_size=300, total_size=300, num_spans=3, align=1)
    assert layout == [(0, 100), (100, 100), (200, 100)]


# ---------------------------------------------------------------------------
# Partial coverage (total_size < dev_size): gaps are expected
# ---------------------------------------------------------------------------

def test_partial_total_size_non_last_spans_are_smaller() -> None:
    # With total_size < dev_size, span sizes are capped by total_size//num_spans.
    # The last span ends at dev_size with gaps preceding it.
    layout = compute_span_layout(dev_size=100, total_size=30, num_spans=3, align=1)
    per_span = 30 // 3
    for _, size in layout[:-1]:
        assert size <= per_span
    pos, size = layout[-1]
    assert pos + size == 100


def test_last_span_always_ends_at_dev_size_even_when_partial() -> None:
    layout = compute_span_layout(dev_size=100, total_size=30, num_spans=3, align=1)
    position, size = layout[-1]
    assert position + size == 100


# ---------------------------------------------------------------------------
# All positions are aligned
# ---------------------------------------------------------------------------

def test_all_positions_are_multiples_of_align() -> None:
    align = 512
    layout = compute_span_layout(dev_size=10000, total_size=10000, num_spans=4, align=align)
    for position, _ in layout:
        assert position % align == 0, f"position {position} is not a multiple of {align}"


def test_all_positions_aligned_with_non_power_of_two() -> None:
    align = 7
    layout = compute_span_layout(dev_size=1000, total_size=1000, num_spans=5, align=align)
    for position, _ in layout:
        assert position % align == 0, f"position {position} is not a multiple of {align}"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_align_equals_dev_size() -> None:
    layout = compute_span_layout(dev_size=1024, total_size=1024, num_spans=1, align=1024)
    assert layout == [(0, 1024)]


def test_large_realistic_disk_full_coverage() -> None:
    # Simulate a 2TiB disk, 4KiB alignment, 3 spans, full coverage
    dev_size = 2 * TiB
    layout = compute_span_layout(dev_size=dev_size, total_size=dev_size, num_spans=3, align=4 * KiB)
    assert spans_cover_device(layout, dev_size)
    for position, _ in layout:
        assert position % (4 * KiB) == 0


def test_large_realistic_disk_partial_coverage() -> None:
    # Simulate a 2TiB disk with write_volume_cap=2GiB
    dev_size = 2 * TiB
    total_size = 2 * GiB
    layout = compute_span_layout(dev_size=dev_size, total_size=total_size, num_spans=3, align=4 * KiB)
    assert layout[0][0] == 0
    pos, size = layout[-1]
    assert pos + size == dev_size
    assert no_overlaps(layout)
    for position, _ in layout:
        assert position % (4 * KiB) == 0
