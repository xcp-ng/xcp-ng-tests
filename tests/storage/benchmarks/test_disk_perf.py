import itertools
import os
import json
import statistics
import subprocess
import pytest
import logging
from datetime import datetime

from helpers import load_results_from_csv, log_result_csv, mean

### Tests default settings ###

CSV_FILE = f"/tmp/results_{datetime.now().strftime("%Y-%m-%d_%H:%M:%S")}.csv"

DEFAULT_SAMPLES_NUM = 10
DEFAULT_SIZE = "1G"
DEFAULT_BS = "4k"
DEFAULT_IODEPTH = 1
DEFAULT_FILE = "fio-testfile"

### Tests parameters

system_memory = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')

block_sizes = ("4k", "16k", "64k", "1M")
file_sizes = ("1G", "4G", f"{(system_memory/(1024.**3))*2}G")
modes = (
        "read",
        "randread",
        "write",
        "randwrite"
)

test_types = {
    "read": "seq_read",
    "randread": "rand_read",
    "write": "seq_write",
    "randwrite": "rand_write"
}

### End of tests parameters ###

def run_fio(
        test_name,
        rw_mode,
        temp_dir,
        bs=DEFAULT_BS,
        iodepth=DEFAULT_IODEPTH,
        size=DEFAULT_SIZE,
        file_path="",
):
    json_output_path = os.path.join(temp_dir, f"{test_name}.json")
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
        "--numjobs=1",
        "--group_reporting",
        "--output-format=json",
        f"--output={json_output_path}"
    ]

    result = subprocess.run(fio_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"fio failed for {test_name}:\n{result.stderr}")

    with open(json_output_path) as f:
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
        diff = (curr-prev if metric == "latency" else prev-curr) / (prev * 100)
        assert diff <= threshold, \
            f"{metric} changed by {diff:.2f}% (allowed {threshold}%)"
        diffs[metric] = diff

    logging.info("Performance difference summary:")
    for k, v in diffs.items():
        sign = "+" if v < 0 else "-"
        logging.info(f"- {k}: {sign}{abs(v):.2f}%")


class TestDiskPerfDestroy: ...


class TestDiskPerf:
    test_cases = itertools.product(block_sizes, file_sizes, modes)

    @pytest.mark.parametrize("block_size,file_size,rw_mode", test_cases)
    def test_disk_benchmark(
            self,
            temp_dir,
            prev_results,
            block_size,
            file_size,
            rw_mode
    ):
        test_type = test_types[rw_mode]
        for i in range(DEFAULT_SAMPLES_NUM):
            result = run_fio(test_type, rw_mode, temp_dir)
            summary = log_result_csv(test_type, rw_mode, result, CSV_FILE)
            assert summary["IOPS"] > 0
        results = load_results_from_csv(CSV_FILE)
        key = (test_type, rw_mode)
        if prev_results and key in prev_results:
            assert_performance_not_degraded(results[key], prev_results[key])
