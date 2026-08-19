from __future__ import annotations

# ruff: noqa: SLF001 - this module deliberately exercises config internals
import pytest

from lib import config
from lib.common import (
    MAX_VDI_SIZE,
    QCOW2_MAX,
    VHD_MAX,
    FormatMax,
    GiB,
    Percent,
    parse_volume_size,
    parse_write_cap,
)

# ---------------------------------------------------------------------------
# parse_volume_size
# ---------------------------------------------------------------------------

def test_parse_volume_size_absolute() -> None:
    assert parse_volume_size("1GiB") == 1 * GiB
    assert parse_volume_size("1024") == 1024

def test_parse_volume_size_symbolic() -> None:
    assert parse_volume_size("VHD_MAX") == VHD_MAX
    assert parse_volume_size("QCOW2_MAX") == QCOW2_MAX

def test_parse_volume_size_format_max() -> None:
    assert parse_volume_size("FORMAT_MAX") == FormatMax.FORMAT_MAX
    assert parse_volume_size("format_max") == FormatMax.FORMAT_MAX

def test_parse_volume_size_invalid() -> None:
    with pytest.raises(ValueError):
        parse_volume_size("not-a-size")

# ---------------------------------------------------------------------------
# parse_write_cap
# ---------------------------------------------------------------------------

def test_parse_write_cap_absolute() -> None:
    assert parse_write_cap("2GiB") == 2 * GiB
    assert parse_write_cap("VHD_MAX") == VHD_MAX

def test_parse_write_cap_format_max() -> None:
    assert parse_write_cap("FORMAT_MAX") == FormatMax.FORMAT_MAX

def test_parse_write_cap_percent() -> None:
    assert parse_write_cap("100%") == Percent(100.0)
    assert parse_write_cap("50%") == Percent(50.0)
    assert parse_write_cap("12.5%") == Percent(12.5)

def test_parse_write_cap_invalid() -> None:
    with pytest.raises(ValueError):
        parse_write_cap("not-a-size")
    with pytest.raises(ValueError):
        parse_write_cap("-5%")

# ---------------------------------------------------------------------------
# config.volume_size / config.write_volume_cap
# ---------------------------------------------------------------------------

@pytest.fixture
def restore_config():
    old_volume_size = config._volume_size
    old_write_volume_cap = config._write_volume_cap
    yield
    config._volume_size = old_volume_size
    config._write_volume_cap = old_write_volume_cap

def test_volume_size_absolute(restore_config) -> None:
    config._volume_size = parse_volume_size("1GiB")
    assert config.volume_size("vhd") == 1 * GiB
    assert config.volume_size("qcow2") == 1 * GiB

def test_volume_size_format_max(restore_config) -> None:
    config._volume_size = parse_volume_size("FORMAT_MAX")
    assert config.volume_size("vhd") == VHD_MAX
    assert config.volume_size("qcow2") == QCOW2_MAX
    assert config.volume_size("vhd") == MAX_VDI_SIZE["vhd"]
    assert config.volume_size("qcow2") == MAX_VDI_SIZE["qcow2"]

def test_volume_size_format_max_raw_errors(restore_config) -> None:
    config._volume_size = parse_volume_size("FORMAT_MAX")
    with pytest.raises(ValueError):
        config.volume_size("raw")

def test_write_volume_cap_absolute(restore_config) -> None:
    config._volume_size = parse_volume_size("10GiB")
    config._write_volume_cap = parse_write_cap("2GiB")
    assert config.write_volume_cap("vhd") == 2 * GiB

def test_write_volume_cap_percent(restore_config) -> None:
    config._volume_size = parse_volume_size("10GiB")
    config._write_volume_cap = parse_write_cap("50%")
    assert config.write_volume_cap("vhd") == 5 * GiB

def test_write_volume_cap_full_percent_of_format_max(restore_config) -> None:
    config._volume_size = parse_volume_size("FORMAT_MAX")
    config._write_volume_cap = parse_write_cap("100%")
    assert config.write_volume_cap("vhd") == VHD_MAX
    assert config.write_volume_cap("qcow2") == QCOW2_MAX

def test_write_volume_cap_format_max(restore_config) -> None:
    config._volume_size = parse_volume_size("FORMAT_MAX")
    config._write_volume_cap = parse_write_cap("FORMAT_MAX")
    assert config.write_volume_cap("vhd") == VHD_MAX
    assert config.write_volume_cap("qcow2") == QCOW2_MAX
