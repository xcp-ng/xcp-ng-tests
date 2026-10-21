from __future__ import annotations

from lib.common import MAX_VDI_SIZE, FormatMax, GiB, Percent, VolumeSizeSpec, WriteCapSpec
from lib.vdi import ImageFormat

ignore_ssh_banner = False
ssh_output_max_lines = 20
_volume_size: VolumeSizeSpec = 1 * GiB
_write_volume_cap: WriteCapSpec = 2 * GiB
write_volume_align = 1

def volume_size(image_format: ImageFormat) -> int:
    match _volume_size:
        case FormatMax.FORMAT_MAX:
            try:
                return MAX_VDI_SIZE[image_format]
            except KeyError:
                raise ValueError(f"FORMAT_MAX has no defined maximum for image format {image_format!r}")
        case int() as size:
            return size

def write_volume_cap(image_format: ImageFormat) -> int:
    match _write_volume_cap:
        case FormatMax.FORMAT_MAX:
            return volume_size(image_format)
        case Percent(pct=pct):
            return int(volume_size(image_format) * pct / 100)
        case int() as cap:
            return cap

def sr_device_config(datakey: str, *, required: list[str] = []) -> dict[str, str]:
    import data  # import here to avoid depending on this user file for collecting tests
    config = getattr(data, datakey)
    for required_field in required:
        if required_field not in config:
            raise Exception(f"{datakey} lacks mandatory {required_field!r}")
    return config
