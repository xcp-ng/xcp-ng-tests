from __future__ import annotations

import textwrap
import types

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from typing import Any, Literal, Union, get_args, get_origin

# Values of the --blktap option: --blktap KEY:VALUE, repeated, or --blktap KEY:VALUE,KEY:VALUE.
# --blktap help lists the keys.

BENCH_MATRICES = Literal["short", "full", "tuning", "tuning-qd64", "tuning-1M", "seq-j1", "seq-j4", "seq",
                         "mix", "crystal"]


class BlktapOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_duration: int = Field(
        default=30, description="Maximum duration, in seconds, of the long running stress tests.")
    bench_outdir: str | None = Field(
        default=None,
        description="Local directory for the results of the benchmarks (default: a new temporary directory).")
    bench_runtime: int = Field(
        default=30, description="Duration, in seconds, of each fio run of the benchmarks.")
    bench_matrix: BENCH_MATRICES = Field(
        default="short",
        description="fio runs: a short subset, the full matrix of blktap-bench, the matrices to tune tapdisk "
        "(tuning, tuning-qd64, tuning-1M) or compare builds (seq-j1, seq-j4, seq; mix: seq with 75/25 random "
        "reads/writes), or the DiskSpd runs of the Windows benchmark (crystal).")
    bench_scheduler: Literal["none", "mq-deadline", "kyber"] | None = Field(
        default=None,
        description="I/O scheduler of the guest for the VBD of the benchmarks (default: the one the guest "
        "kernel chooses: mq-deadline with a single queue, none with several).")
    bench_image_dir: str = Field(
        default="/tmp",
        description="dom0 directory of the image of the fio benchmark (e.g. a filesystem on a ramdisk), or a "
        "block device.")
    bench_image_size: int | None = Field(
        default=None,
        description="Size, in MiB, of the image of the fio benchmark (default: 10240 for raw, 4096 otherwise).")
    bench_image_vg: str | None = Field(
        default=None,
        description="Volume group in which the raw image of the fio benchmark is allocated, as the LV "
        "pytest-bench removed at the end of the test (default: a file in bench-image-dir).")
    bench_qcow2_driver: Literal["qcow2", "tdrs"] = Field(
        default="qcow2",
        description="tapdisk driver serving the qcow2 image of the benchmarks: qcow2 (qemu's), or tdrs "
        "(blktap-rs', in a tapdisk built with --with-tdrs).")
    polling_duration: int | None = Field(
        default=None,
        description="polling-duration (us) of the VBDs connected by the tests (default: the XCP-ng one).")
    polling_idle_threshold: int | None = Field(
        default=None,
        description="polling-idle-threshold (% of dom0 idle CPU) of the VBDs connected by the tests "
        "(default: the XCP-ng one).")


def _type_name(annotation: object) -> str:
    if get_origin(annotation) in (Union, types.UnionType):
        annotation = next(a for a in get_args(annotation) if a is not type(None))
    if get_origin(annotation) is Literal:
        return "|".join(str(value) for value in get_args(annotation))
    return str(getattr(annotation, "__name__", annotation))


def _key(name: str) -> str:
    return name.replace("_", "-")


def blktap_options_help() -> str:
    lines = ["Options of the blktap tests: --blktap KEY:VALUE, repeated or separated by commas",
             "(--blktap KEY:VALUE --blktap KEY:VALUE, or --blktap KEY:VALUE,KEY:VALUE);",
             ""]
    for name, field in BlktapOptions.model_fields.items():
        default = "" if field.default is None else f" (default: {field.default})"
        lines.append(f"  {_key(name)}: {_type_name(field.annotation)}{default}")
        lines.extend(textwrap.wrap(field.description or "", width=100, initial_indent="      ",
                                   subsequent_indent="      "))
    return "\n".join(lines)


def parse_blktap_options(arguments: list[str]) -> BlktapOptions:
    """Build the options from the values of --blktap, raise ValueError if one is wrong."""
    fields = BlktapOptions.model_fields
    values: dict[str, Any] = {}
    for argument in arguments:
        for item in argument.split(","):
            key, separator, value = item.partition(":")
            name = key.strip().replace("-", "_")
            value = value.strip()
            if not separator or name not in fields:
                raise ValueError(
                    f"--blktap {item!r}: expected KEY:VALUE, with KEY among "
                    f"{', '.join(_key(n) for n in fields)} (--blktap help describes them)")
            if name in values:
                raise ValueError(f"--blktap {key.strip()} given twice")
            values[name] = value
    try:
        return BlktapOptions(**values)
    except ValidationError as e:
        problems = "; ".join(f"{_key(str(error['loc'][0]))}: {error['msg']}" for error in e.errors())
        raise ValueError(f"--blktap: {problems}") from e
