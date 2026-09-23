import csv
import os
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from typing import Any, Literal

FIOTestMode = Literal["read", "randread", "write", "randwrite"]


class FioLatency(BaseModel):
    min: float
    max: float
    mean: float
    stddev: float
    N: float
    percentile: dict[float, int] | None = None


class FioSync(BaseModel):
    total_ios: int
    lat_ns: FioLatency


class FioStats(BaseModel):
    io_bytes: int
    io_kbytes: int
    bw_bytes: int
    bw: int
    iops: float
    runtime: int
    total_ios: int
    short_ios: int
    drop_ios: int
    slat_ns: FioLatency
    clat_ns: FioLatency
    lat_ns: FioLatency
    bw_min: int
    bw_max: int
    bw_agg: float
    bw_mean: float
    bw_dev: float
    bw_samples: int
    iops_min: int
    iops_max: int
    iops_mean: float
    iops_stddev: float
    iops_samples: int


class FioJobOptions(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    rw: FIOTestMode
    bs: str
    iodepth: str
    size: str
    filename: Path
    direct: bool | None = None
    end_fsync: bool | None = None
    fsync_on_close: bool | None = None
    numjobs: int | None = None


class FioJob(BaseModel):
    jobname: str
    groupid: int
    error: int
    eta: int
    elapsed: int
    job_options: FioJobOptions = Field(alias="job options")
    read: FioStats
    write: FioStats
    trim: FioStats
    sync: FioSync
    job_runtime: int
    usr_cpu: float
    sys_cpu: float
    ctx: int
    majf: int
    minf: int
    iodepth_level: dict[str, float]
    iodepth_submit: dict[str, float]
    iodepth_complete: dict[str, float]
    latency_ns: dict[str, float]
    latency_us: dict[str, float]
    latency_ms: dict[str, float]
    latency_depth: int
    latency_target: int
    latency_percentile: float
    latency_window: int


class FioDiskUtil(BaseModel):
    name: str
    read_ios: int
    write_ios: int
    read_merges: int
    write_merges: int
    read_ticks: int
    write_ticks: int
    in_queue: int
    util: float


class FioResultJson(BaseModel):
    fio_version: str = Field(alias="fio version")
    timestamp: datetime
    timestamp_ms: datetime
    time: datetime
    jobs: list[FioJob]
    disk_util: list[FioDiskUtil]

    @field_validator("time", mode="before")
    @staticmethod
    def parse_fio_time_string(value: str) -> datetime:
        return datetime.strptime(value, "%a %b %d %H:%M:%S %Y")


class FioBenchmarkCSV(BaseModel):
    timestamp: datetime
    test_name: str = Field(alias="test")
    mode: FIOTestMode
    bandwidth_mbps: float = Field(alias="bw_MBps")
    iops: float = Field(alias="IOPS")
    latency: float


def log_result_csv(
        test_type: str,
        rw_mode: FIOTestMode,
        result_json: FioResultJson,
        csv_path: Path | str
) -> FioBenchmarkCSV:
    assert len(result_json.jobs) >= 1

    op_data: FioStats = getattr(result_json.jobs[0], rw_mode.replace("rand", ""))
    benchmark = FioBenchmarkCSV(
        timestamp=datetime.now(),
        test=test_type,
        mode=rw_mode,
        bw_MBps=round(op_data.bw / 1024, 2),
        IOPS=round(op_data.iops, 2),
        latency=round(op_data.lat_ns.mean, 2),
    )

    result = benchmark.model_dump()
    file_exists = os.path.exists(csv_path)
    with open(csv_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=result.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(result)

    return benchmark


def load_results_from_csv(csv_path: Path | str) -> dict[str, list[FioBenchmarkCSV]]:
    results: dict[str, list[FioBenchmarkCSV]] = defaultdict(list)
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            results[row["test"]].append(FioBenchmarkCSV.model_validate(row))
    return dict(results)


def mean(data: list[FioBenchmarkCSV], key: str) -> float:
    values = [
        float(val) for x in data
        if (val := getattr(x, key, None)) is not None
    ]

    if not values:
        return 0.0

    return statistics.mean(values)
