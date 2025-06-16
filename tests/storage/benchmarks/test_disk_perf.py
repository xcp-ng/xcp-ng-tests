import json
import logging
import os
import statistics
from datetime import datetime

import pytest

from lib.commands import SSHCommandFailed

from .helpers import load_results_from_csv, log_result_csv, mean

# Tests default settings #

CSV_FILE = f"/tmp/results_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.csv"

DEFAULT_SAMPLES_NUM = 10
DEFAULT_SIZE = "1G"
DEFAULT_BS = "4k"
DEFAULT_IODEPTH = 1
DEFAULT_NUMJOBS = 1
DEFAULT_FILE = "fio-testfile"


def run_fio(
    vm,
    test_name,
    rw_mode,
    temp_dir,
    local_temp_dir,
    bs=DEFAULT_BS,
    iodepth=DEFAULT_IODEPTH,
    size=DEFAULT_SIZE,
    numjobs=DEFAULT_NUMJOBS,
    file_path="",
):
    json_output_path = os.path.join(temp_dir, f"{test_name}.json")
    local_json_path = os.path.join(local_temp_dir, f"{test_name}.json")
    if not file_path:
        file_path = os.path.join(temp_dir, DEFAULT_FILE)
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
        vm.ssh(fio_cmd, check=True)
    except SSHCommandFailed as e:
        raise RuntimeError(f"fio failed for {test_name}:{e}")
    vm.scp(json_output_path, local_json_path, local_dest=True)
    logging.debug(f"Stored json at {local_json_path}")
    with open(local_json_path) as f:
        return json.load(f)


def assert_performance_not_degraded(current, previous, threshold=10):
    diffs = {}
    for metric in ("bw_MBps", "IOPS", "latency"):
        try:
            curr = mean(current, metric)
            prev = mean(previous, metric)
        except statistics.StatisticsError:
            logging.info(f"Missing metric ({metric}), skipping comparison")
            continue
        diff = (curr - prev if metric == "latency" else prev - curr) / (prev * 100)
        assert (
            diff <= threshold
        ), f"{metric} changed by {diff:.2f}% (allowed {threshold}%)"
        diffs[metric] = diff

    logging.info("Performance difference summary:")
    for k, v in diffs.items():
        sign = "+" if v < 0 else "-"
        logging.info(f"- {k}: {sign}{abs(v):.2f}%")


class TestDiskPerf:

    @pytest.mark.parametrize("block_size,file_size,rw_mode", test_cases)
    def test_disk_benchmark(
        self,
        pytestconfig,
        temp_dir,
        local_temp_dir,
        prev_results,
        block_size,
        file_size,
        rw_mode,
        running_unix_vm_with_fio,
        plugged_vbd,
        image_format,
    ):
        vm = running_unix_vm_with_fio
        vbd = plugged_vbd
        device = f"/dev/{vbd.param_get(param_name='device')}"
        test_type = "{}-{}-{}-{}".format(block_size, file_size, rw_mode, image_format)

        for i in range(DEFAULT_SAMPLES_NUM):
            result = run_fio(
                vm,
                test_type,
                rw_mode,
                temp_dir,
                local_temp_dir,
                file_path=device,
                bs=block_size,
                size=file_size,
                iodepth=pytestconfig.getoption("iodepth"),
                numjobs=pytestconfig.getoption("numjobs"),
            )
            summary = log_result_csv(test_type, rw_mode, result, CSV_FILE)
            assert summary["IOPS"] > 0
        key = (test_type, rw_mode)
        if prev_results and key in prev_results:
            results = load_results_from_csv(CSV_FILE)
            assert_performance_not_degraded(results[key], prev_results[key])
