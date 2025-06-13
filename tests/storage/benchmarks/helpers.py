import os
import statistics
import csv
from datetime import datetime


def log_result_csv(test_type, rw_mode, result_json, csv_path):
    job = result_json["jobs"][0]
    op_data = job[rw_mode.replace("rand", "")]
    bw_kbps = op_data["bw"]
    iops = op_data["iops"]
    latency = op_data["lat_ns"]["mean"]

    result = {
        "timestamp": datetime.now().isoformat(),
        "test": test_type,
        "mode": rw_mode,
        "bw_MBps": round(bw_kbps / 1024, 2),
        "IOPS": round(iops, 2),
        "latency": round(latency, 2),
    }

    file_exists = os.path.exists(csv_path)
    with open(csv_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=result.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(result)

    return result


def load_results_from_csv(csv_path):
    results = {}
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = (row["test"], row["mode"])
            if key not in results:
                results[key] = []
            results[key].append(row)
    return results


def mean(data, key):
    return statistics.mean(
        [float(x[key]) for x in data if key in x]
    )