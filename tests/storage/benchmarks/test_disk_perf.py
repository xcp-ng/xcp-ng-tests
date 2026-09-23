import pytest

import json
import logging
import shlex
import statistics
from pathlib import Path

from lib.commands import SSHCommandFailed
from lib.vbd import VBD
from lib.vdi import VDI
from lib.vm import VM

from .conftest import temp_dir
from .helpers import FioBenchmarkCSV, FioResultJson, FIOTestMode, load_results_from_csv, log_result_csv, mean

from typing import get_args

DEFAULT_SAMPLES_NUM = 10
DEFAULT_SIZE = "1G"
DEFAULT_BS = "4k"
DEFAULT_IODEPTH = 1
DEFAULT_NUMJOBS = 1
DEFAULT_FILE = "fio-testfile"


def run_fio(
    vm: VM,
    test_name: str,
    rw_mode: str,
    temp_dir: Path,
    local_temp_dir: Path,
    bs: str = DEFAULT_BS,
    iodepth: int = DEFAULT_IODEPTH,
    size: str = DEFAULT_SIZE,
    numjobs: int = DEFAULT_NUMJOBS,
    file_path: Path | None = None,
) -> FioResultJson:
    json_output_path = temp_dir / f"{test_name}.json"
    local_json_path = local_temp_dir / f"{test_name}.json"
    if not file_path:
        file_path = temp_dir / DEFAULT_FILE
    fio_cmd = [
        "fio",
        f"--name={test_name}",
        f"--rw={rw_mode}",
        f"--bs={bs}",
        f"--iodepth={iodepth}",
        f"--size={size}",
        f"--filename={file_path}",
        "--direct=1",
        "--end_fsync=1",
        "--fsync_on_close=1",
        f"--numjobs={numjobs}",
        "--group_reporting",
        "--output-format=json",
        f"--output={json_output_path}",
    ]
    logging.debug(f"Running {fio_cmd}")
    try:
        vm.ssh(shlex.join(fio_cmd), check=True)
    except SSHCommandFailed as e:
        raise RuntimeError(f"fio failed for {test_name}:{e}")
    vm.scp(json_output_path.as_posix(), local_json_path.as_posix(), local_dest=True)
    logging.debug(f"Stored json at {local_json_path}")
    with open(local_json_path) as f:
        return FioResultJson.model_validate_json(f.read())


def assert_performance_not_degraded(
    current: list[FioBenchmarkCSV],
    previous: list[FioBenchmarkCSV],
    regression_threshold: int = 10,
    improvement_threshold: int = 10
) -> None:
    diffs = {}
    for metric in ("bandwidth_mbps", "iops", "latency"):
        try:
            curr = mean(current, metric)
            prev = mean(previous, metric)
        except statistics.StatisticsError:
            logging.info(f"Missing metric ({metric}), skipping comparison")
            continue
        diff = (curr - prev if metric == "latency" else prev - curr) / prev * 100
        if diff < improvement_threshold:
            logging.info(f"{metric} improved by {abs(diff):.2f}%")
        assert diff <= regression_threshold, f"{metric} regressed by {diff:.2f}% (allowed {regression_threshold}%)"
        diffs[metric] = diff

    logging.info("Performance difference summary:")
    for k, v in diffs.items():
        sign = "+" if v < 0 else "-"
        logging.info(f"- {k}: {sign}{abs(v):.2f}%")


class TestDiskPerf:
    @pytest.mark.parametrize("rw_mode", get_args(FIOTestMode))
    @pytest.mark.parametrize("block_size", ["4k"])
    @pytest.mark.parametrize("file_size", ["1G"])
    @pytest.mark.small_vm
    def test_disk_benchmark(
        self,
        temp_dir: Path,
        local_temp_dir: Path,
        result_csv_file: Path,
        prev_results: dict[str, list[FioBenchmarkCSV]],
        block_size: str,
        file_size: str,
        rw_mode: FIOTestMode,
        running_unix_vm_with_fio: VM,
        plugged_vbd: VBD,
        vdi_on_local_sr: VDI,
    ) -> None:
        vm = running_unix_vm_with_fio
        vbd = plugged_vbd
        vdi = vdi_on_local_sr
        device = Path(f"/dev/{vbd.param_get(param_name='device')}")
        test_type = f"bench-fio-{block_size}-{file_size}-{rw_mode}-{vdi.get_image_format()}"

        for _ in range(DEFAULT_SAMPLES_NUM):
            result = run_fio(
                vm,
                test_type,
                rw_mode,
                temp_dir,
                local_temp_dir,
                file_path=device,
                bs=block_size,
                size=file_size,
                iodepth=1,
                numjobs=1,
            )
            summary = log_result_csv(test_type, rw_mode, result, result_csv_file)
            assert summary.iops > 0
        if prev_results and test_type in prev_results:
            results = load_results_from_csv(result_csv_file)
            assert_performance_not_degraded(
                results[test_type],
                prev_results[test_type],
                regression_threshold=10,
                improvement_threshold=10,
            )
