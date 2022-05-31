#!/usr/bin/env -S python3 -u

import argparse
import json
import subprocess
import sys

JOBS = {
    "main": {
        "description": "a group of not-too-long tests that run either without a VM, or with a single small one",
        "requirements": [
            "A pool with at least 2 hosts, each with a local SR and a shared SR.",
            "A second pool with a SR to receive migrated VMs.",
            "An additional free disk on the first host.",
            "Config in data.py for another NFS SR.",
            "A VM (small and fast-booting).",
        ],
        "nb_pools": 2,
        "params": {
            "--vm": "single/small_vm",
            "--sr-disk": "auto",
        },
        "paths": [
            "tests/misc",
            "tests/migration",
            "tests/snapshot",
            "tests/xapi-plugins",
        ],
        "markers": "(small_vm or no_vm) and not flaky and not reboot and not complex_prerequisites",
    },
    "main-multi": {
        "description": "a group of tests that need to run on the largest variety of VMs",
        "requirements": [
            "A pool with at least 2 hosts, each with a local SR and a shared SR.",
            "An additional free disk on the first host.",
            "A second pool with a SR to receive migrated VMs.",
            "VMs of all sorts (unix, windows, HVM, PV, PV-shim, BIOS, UEFI...).",
        ],
        "nb_pools": 2,
        "params": {
            "--vm[]": "multi/all",
            "--sr-disk": "auto",
        },
        "paths": ["tests/misc"],
        "markers": "multi_vms and not flaky and not reboot",
    },
    "quicktest": {
        "description": "XAPI's quicktest, not so quick by the way",
        "requirements": [
            "Any pool.",
            "Execution depends on the SRs present, as each SR from the pool will get tested.",
        ],
        "nb_pools": 1,
        "params": {},
        "paths": ["tests/quicktest"],
    },
    "storage-main": {
        "description": "tests all storage drivers (except linstor), but avoids migrations and reboots",
        "requirements": [
            "A pool with at least 3 hosts.",
            "An additional free disk on every host.",
            "Configuration in data.py for each remote SR that will be tested.",
            "A small VM that can be imported on the SRs.",
        ],
        "nb_pools": 1,
        "params": {
            "--vm": "single/small_vm",
            "--sr-disk": "auto",
        },
        "paths": ["tests/storage"],
        "markers": "(small_vm or no_vm) and not reboot",
        "name_filter": "not migration and not linstor",
    },
    "storage-migrations": {
        "description": "tests migrations with all storage drivers (except linstor)",
        "requirements": [
            "A pool with at least 3 hosts.",
            "An additional free disk on every host.",
            "A second pool with at least 1 host and a SR to receive VMs.",
            "Configuration in data.py for each remote SR that will be tested.",
            "A small VM that can be imported on the SRs.",
        ],
        "nb_pools": 2,
        "params": {
            "--vm": "single/small_vm",
            "--sr-disk": "auto",
        },
        "paths": ["tests/storage"],
        "markers": "",
        "name_filter": "migration and not linstor",
    },
    "storage-reboots": {
        "description": "storage driver tests that involve rebooting hosts (except linstor and flaky tests)",
        "requirements": [
            "A pool with at least 3 hosts, whose master host can be rebooted (best if reboots fast).",
            "An additional free disk on every host.",
            "Configuration in data.py for each remote SR that will be tested.",
            "A small VM that can be imported on the SRs.",
        ],
        "nb_pools": 1,
        "params": {
            "--vm": "single/small_vm",
            "--sr-disk": "auto",
        },
        "paths": ["tests/storage"],
        "markers": "reboot and not flaky",
        "name_filter": "not linstor",
    },
    "sb-main": {
        "description": "tests uefistored and SecureBoot using a small unix VM (or no VM when none needed)",
        "requirements": [
            "A pool >= 8.2.1. One host is enough.",
            "A fast-booting unix UEFI VM with efitools.",
            "See README.md for requirements on the test runner itself.",
        ],
        "nb_pools": 1,
        "params": {
            "--vm": "single/small_vm_efitools",
        },
        "paths": ["tests/uefistored"],
        "markers": "not windows_vm",
    },
    "sb-windows": {
        "description": "tests uefistored and SecureBoot using a Windows VM",
        "requirements": [
            "A pool >= 8.2.1. One host is enough.",
            "A (small if possible) Windows UEFI VM.",
        ],
        "nb_pools": 1,
        "params": {
            "--vm": "single/small_vm_windows",
        },
        "paths": ["tests/uefistored"],
        "markers": "windows_vm",
    },
    "sb-unix-multi": {
        "description": "checks basic Secure-Boot support on a variety of Unix VMs",
        "requirements": [
            "A pool >= 8.2.1. One host is enough.",
            "A variety of UEFI Unix VMs.",
            "See README.md for requirements on the test runner itself.",
        ],
        "nb_pools": 1,
        "params": {
            "--vm[]": "multi/uefi_unix",
        },
        "paths": ["tests/uefistored"],
        "markers": "multi_vms and unix_vm",
    },
    "sb-windows-multi": {
        "description": "checks basic Secure-Boot support on a variety of Windows VMs",
        "requirements": [
            "A pool >= 8.2.1. One host is enough.",
            "A variety of UEFI Windows VMs.",
        ],
        "nb_pools": 1,
        "params": {
            "--vm[]": "multi/uefi_windows",
        },
        "paths": ["tests/uefistored"],
        "markers": "multi_vms and windows_vm",
    },
    "tools-unix": {
        "description": "tests our unix guest tools on a single small VM",
        "requirements": [
            "A pool with at least 2 hosts.",
            "A local SR on the second host",
            "A small and fast-booting unix VM whose OS is supported by our tools installer.",
        ],
        "nb_pools": 1,
        "params": {
            "--vm": "single/small_vm_efitools",
        },
        "paths": ["tests/guest-tools/unix"],
        "markers": "",
    },
    "tools-unix-multi": {
        "description": "tests our unix guest tools on a variety of VMs",
        "requirements": [
            "A pool with at least 2 hosts.",
            "A local SR on the second host",
            "A variety of unix VMs whose OSes are supported by our tools installer.",
        ],
        "nb_pools": 1,
        "params": {
            "--vm[]": "multi/tools_unix",
        },
        "paths": ["tests/guest-tools/unix"],
        "markers": "multi_vms",
    },
    "flaky": {
        "description": "tests that usually pass, but sometimes fail unexpectedly",
        "requirements": [
            "Will vary depending on the tests included.",
            "Use the collect command to get the list of tests "
            + "and check the requirements written at the top of the test files.",
        ],
        "nb_pools": 1,
        "params": {
            "--vm": "single/small_vm",
            "--sr-disk": "auto",
        },
        "paths": ["tests"],
        "markers": "flaky",
    },
}

BROKEN_TESTS = [
    "tests/storage/linstor", # needs updating and fixing
    "tests/misc/test_update_host.py", # doesn't test anything currently unless the host is out of date
    "tests/migration/test_host_evacuate.py::TestHostEvacuateWithNetwork" # not really broken but we'll handle it later
]

def get_vm_or_vms_refs(handle):
    try:
        from vm_data import VMS
    except ImportError:
        print("ERROR: Could not import VMS from vm_data.py.")
        print("Get the latest vm_data.py from XCP-ng's internal lab or copy data.py-dist and fill with your VM refs.")
        print("You may also bypass this error by providing your own --vm parameter(s).")
        sys.exit(1)

    category, key = handle.split("/")
    if category not in VMS or not VMS[category].get(key):
        print(f"ERROR: Could not find VMS['{category}']['{key}'] in vm_data.py, or it's empty.")
        print("You need to update your local vm_data.py.")
        print("You may also bypass this error by providing your own --vm parameter(s).")
        sys.exit(1)
    return VMS[category][key]

def build_pytest_cmd(job_data, hosts=None, pytest_args=[]):
    markers = job_data.get("markers", None)
    name_filter = job_data.get("name_filter", None)

    job_params = dict(job_data["params"])

    # pytest_args may override job_params
    pytest_args_keys = []
    for arg in pytest_args:
        if "=" in arg:
            pytest_args_keys.append(arg.split("=")[0])
    for key, value in job_data["params"].items():
        if key.rstrip("[]") in pytest_args_keys:
            del job_params[key]

    cmd = ["pytest"] + job_data["paths"]
    if markers:
        cmd += ["-m", markers]
    if name_filter:
        cmd += ["-k", name_filter]
    if hosts:
        cmd.append(f"--hosts={hosts}")
    for key, value in job_params.items():
        if key == "--vm[]":
            vms = get_vm_or_vms_refs(value)
            for vm_ref in vms:
                cmd.append(f"--vm={vm_ref}")
        elif key == "--vm":
            cmd.append(f"--vm={get_vm_or_vms_refs(value)}")
        else:
            cmd.append(f"{key}={value}")
    cmd += pytest_args
    return cmd

def action_list(args):
    for job, data in JOBS.items():
        print(f"{job}: {data['description']}")

def action_show(args):
    print(json.dumps(JOBS[args.job], indent=4))

def action_collect(args):
    cmd = build_pytest_cmd(JOBS[args.job], None, ["--collect-only"] + args.pytest_args)
    subprocess.run(cmd)

def action_check(args):
    error = False

    def extract_tests(cmd):
        tests = set()
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode != 0 and res.returncode != 5: # 5 means no test found
            print(f"ERROR: Command failed {cmd}: {res.stderr.decode().strip()}")
            sys.exit(1)
        for line in res.stdout.decode().splitlines():
            if line.startswith("tests/"):
                tests.add(line.split("[")[0])
        return tests

    broken_tests = set()
    for path in BROKEN_TESTS:
        broken_tests |= extract_tests(["pytest", path, "--collect-only", "-q"])

    all_tests = extract_tests(["pytest", "--collect-only", "-q"]) - broken_tests

    print("*** Checking that all tests are selected by at least one job... ", end="")
    job_tests = set()
    for job_data in JOBS.values():
        job_tests |= extract_tests(build_pytest_cmd(job_data, None, ["--collect-only", "-q", "--vm=a_vm"]))
    tests_without_jobs = sorted(list(all_tests - job_tests))
    if tests_without_jobs:
        error = True
        print("FAILED")
        print("\nThese tests were not selected by any job:\n- " + "\n- ".join(tests_without_jobs) + "\n")
    else:
        print("OK")

    print("*** Checking that all tests that use VMs have VM target markers (small_vm, etc.)... ", end="")
    tests_missing_vm_markers = extract_tests(
        ["pytest", "--collect-only", "-q", "-m", "not no_vm and not (small_vm or multi_vm or big_vm)"]
    )
    if tests_missing_vm_markers:
        error = True
        print("FAILED")
        print("\nThese tests are missing VM target markers (small_vm, multi_vm, etc.):\n- "
              + "\n- ".join(tests_missing_vm_markers) + "\n")
    else:
        print("OK")

    print("*** Checking that all tests marked multi_vms are selected in a job that runs on multiple VMs... ", end="")
    multi_vm_tests = extract_tests(["pytest", "--collect-only", "-q", "-m", "multi_vms"]) - broken_tests
    job_tests = set()
    for job_data in JOBS.values():
        if "--vm[]" in job_data["params"]:
            job_tests |= extract_tests(build_pytest_cmd(job_data, None, ["--collect-only", "-q", "--vm=a_vm"]))
    tests_missing = sorted(list(multi_vm_tests - job_tests))
    if tests_missing:
        error = True
        print("FAILED")
        print("\nThese tests should be in a job that runs on multiple VMs:\n- " + "\n- ".join(tests_missing) + "\n")
    else:
        print("OK")

    if error:
        sys.exit(1)

def action_run(args):
    cmd = build_pytest_cmd(JOBS[args.job], args.hosts, args.pytest_args)
    print(subprocess.list2cmdline(cmd))
    if args.print_only:
        return

    # check that enough pool masters have been provided
    nb_pools = len(args.hosts.split(","))
    if nb_pools < JOBS[args.job]["nb_pools"]:
        print(f"Error: only {nb_pools} master host(s) provided, {JOBS[args.job]['nb_pools']} required.")
        sys.exit(1)

    res = subprocess.run(cmd)
    if res.returncode:
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Manage test jobs")
    subparsers = parser.add_subparsers(dest="action", metavar="action")
    subparsers.required = True

    list_parser = subparsers.add_parser("list", help="list available jobs.")
    list_parser.set_defaults(func=action_list)

    run_parser = subparsers.add_parser("show", help="show details about a job definition.")
    run_parser.add_argument("job", help="name of the job.", choices=JOBS.keys(), metavar="job")
    run_parser.set_defaults(func=action_show)

    run_parser = subparsers.add_parser("collect", help="show test collection based on the job definition.")
    run_parser.add_argument("job", help="name of the job.", choices=JOBS.keys(), metavar="job")
    run_parser.add_argument("pytest_args", nargs=argparse.REMAINDER,
                            help="all additional arguments after the last positional argument will "
                                 "be passed to pytest and replace default job params if needed.")
    run_parser.set_defaults(func=action_collect)

    run_parser = subparsers.add_parser("check", help="run sanity checks on the tests and jobs.")
    run_parser.set_defaults(func=action_check)

    run_parser = subparsers.add_parser("run", help="run a job.")
    run_parser.add_argument("--print-only", "-p", action="store_true",
                            help="print the command, but don't run it. Must be specified before positional arguments.")
    run_parser.add_argument("job", help="name of the job to run.", choices=JOBS.keys(), metavar="job")
    run_parser.add_argument("hosts", help="master host(s) of pools to run the tests on, comma-separated.")
    run_parser.add_argument("pytest_args", nargs=argparse.REMAINDER,
                            help="all additional arguments after the last positional argument will "
                                 "be passed to pytest and replace default job params if needed.")
    run_parser.set_defaults(func=action_run)

    args = parser.parse_args()
    args.func(args)

if __name__ == '__main__':
    main()
