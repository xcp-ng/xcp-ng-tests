"""Size constants and parsing helpers."""

KiB = 2**10
MiB = KiB**2
GiB = KiB**3
TiB = KiB**4

VHD_MAX = 2040 * GiB
QCOW2_MAX = 16 * TiB - 2561 * MiB

_SYMBOLIC_SIZES: dict[str, int] = {
    'VHD_MAX': VHD_MAX,
    'QCOW2_MAX': QCOW2_MAX,
}


def parse_size(size_str: str) -> int:
    """
    Parse a size string like "2.5TiB", "1GiB", "1024", "VHD_MAX", or "QCOW2_MAX".
    """
    symbolic = _SYMBOLIC_SIZES.get(size_str.strip().upper())
    if symbolic is not None:
        return symbolic
    try:
        return int(size_str)
    except ValueError:
        pass

    size_str = size_str.strip()
    for unit, multiplier in [('TiB', TiB), ('GiB', GiB), ('MiB', MiB), ('KiB', KiB)]:
        if size_str.endswith(unit):
            try:
                return int(float(size_str[:-len(unit)].strip()) * multiplier)
            except ValueError:
                pass

    raise ValueError(f"Cannot parse size: {size_str}")
