from __future__ import annotations

import textwrap
import types

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from typing import Any, Literal, Union, get_args, get_origin

# Values of the --blktap option: --blktap KEY:VALUE, repeated, or --blktap KEY:VALUE,KEY:VALUE. A key
# of a list option (workloads) is repeated to give several values. --blktap help lists the keys.

BENCH_MATRICES = Literal["short", "full", "tuning", "tuning-qd64", "tuning-1M", "seq-j1", "seq-j4", "seq",
                         "mix", "crystal"]
APP_WORKLOADS = Literal["pgbench", "sqlite-delete", "sqlite-wal", "fileserver", "varmail", "webserver", "kernel"]


class BlktapOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_duration: int = Field(
        default=30, description="Maximum duration, in seconds, of the long running stress tests.")
    destroy_iterations: int = Field(
        default=10, description="Number of tapdisks destroyed during guest I/O by the hot-unplug test.")
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
    app_bench_vcpus: int = Field(
        default=8, description="vCPUs of the VM of the application benchmark, and jobs of its kernel build.")
    app_bench_workloads: list[APP_WORKLOADS] = Field(
        default=["pgbench", "sqlite-delete", "sqlite-wal", "fileserver", "varmail", "webserver", "kernel"],
        description="Workloads of the application benchmark; repeat the key to choose several (sqlite-wal and "
        "sqlite-delete run speedtest1 with each journal mode, fileserver, varmail and webserver are filebench "
        "personalities, kernel is a Linux kernel build).")
    polling_duration: int | None = Field(
        default=None,
        description="polling-duration (us) of the VBDs connected by the tests (default: the XCP-ng one).")
    polling_idle_threshold: int | None = Field(
        default=None,
        description="polling-idle-threshold (% of dom0 idle CPU) of the VBDs connected by the tests "
        "(default: the XCP-ng one).")


def _is_list(annotation: object) -> bool:
    return get_origin(annotation) is list


def _type_name(annotation: object) -> str:
    if get_origin(annotation) in (Union, types.UnionType):
        annotation = next(argument for argument in get_args(annotation) if argument is not type(None))
    if _is_list(annotation):
        return "list of " + _type_name(get_args(annotation)[0])
    if get_origin(annotation) is Literal:
        return "|".join(str(value) for value in get_args(annotation))
    return str(getattr(annotation, "__name__", annotation))


def _key(name: str) -> str:
    return name.replace("_", "-")


def blktap_options_help() -> str:
    lines = ["Options of the blktap tests: --blktap KEY:VALUE, repeated or separated by commas",
             "(--blktap KEY:VALUE --blktap KEY:VALUE, or --blktap KEY:VALUE,KEY:VALUE);",
             "repeat the key of a list option to give several values.", ""]
    for name, field in BlktapOptions.model_fields.items():
        if field.default is None:
            default = ""
        elif isinstance(field.default, list):
            default = f" (default: {', '.join(field.default)})"
        else:
            default = f" (default: {field.default})"
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
                    f"{', '.join(_key(field_name) for field_name in fields)} (--blktap help describes them)")
            if _is_list(fields[name].annotation):
                values.setdefault(name, []).append(value)
            elif name in values:
                raise ValueError(f"--blktap {key.strip()} given twice")
            else:
                values[name] = value
    try:
        return BlktapOptions(**values)
    except ValidationError as e:
        problems = "; ".join(f"{_key(str(error['loc'][0]))}: {error['msg']}" for error in e.errors())
        raise ValueError(f"--blktap: {problems}") from e
