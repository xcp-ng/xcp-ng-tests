#!/usr/bin/env -S python3 -u

import argparse
import json
import subprocess
import sys

from lib.commands import ssh

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
            "tests/security",
            "tests/migration",
            "tests/network",
            "tests/snapshot",
            "tests/system",
            "tests/xapi",
            "tests/xapi_plugins",
            "tests/install/test_fixtures.py",
        ],
        "markers": "(small_vm or no_vm) and not flaky and not reboot and not complex_prerequisites",
    },
    "main-multi-unix": {
        "description": "a group of tests that need to run on the largest variety of VMs - unix split",
        "requirements": [
            "A pool with at least 2 hosts, each with a local SR and a shared SR.",
            "An additional free disk on the first host.",
            "A second pool with a SR to receive migrated VMs.",
            "Unix VMs of all sorts (HVM, PV, PV-shim, BIOS, UEFI...).",
        ],
        "nb_pools": 2,
        "params": {
            "--vm[]": "multi/all_unix",
            "--sr-disk": "auto",
        },
        "paths": ["tests/misc", "tests/migration"],
        "markers": "multi_vms and not flaky and not reboot",
    },
    "main-multi-windows": {
        "description": "a group of tests that need to run on the largest variety of VMs - windows split",
        "requirements": [
            "A pool with at least 2 hosts, each with a local SR and a shared SR.",
            "An additional free disk on the first host.",
            "A second pool with a SR to receive migrated VMs.",
            "Windows VMs of all sorts (HVM, PV, PV-shim, BIOS, UEFI...).",
        ],
        "nb_pools": 2,
        "params": {
            "--vm[]": "multi/all_windows",
            "--sr-disk": "auto",
        },
        "paths": ["tests/misc", "tests/migration"],
        "markers": "multi_vms and not flaky and not reboot",
    },
    "packages": {
        "description": "tests that packages can be installed correctly",
        "requirements": [
            "Any pool.",
        ],
        "nb_pools": 1,
        "params": {},
        "paths": ["tests/packages"],
        "markers": "",
    },
    "storage-main": {
        "description": "tests all storage drivers, but avoids migrations and reboots",
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
        "markers": "(small_vm or no_vm) and not reboot and not quicktest and not sr_disk_4k",
        "name_filter": "not migration and not linstor",
    },
    "storage-migrations": {
        "description": "tests migrations with all storage drivers",
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
        "markers": "not sr_disk_4k",
        "name_filter": "migration and not linstor",
    },
    "storage-reboots": {
        "description": "storage driver tests that involve rebooting hosts (except flaky tests)",
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
        "markers": "reboot and not flaky and not sr_disk_4k",
        "name_filter": "not linstor",
    },
    "storage-quicktest": {
        "description": "runs `quicktest` on all storage drivers",
        "requirements": [
            "A pool with at least 3 hosts.",
            "An additional free disk on every host.",
            "Configuration in data.py for each remote SR that will be tested.",
        ],
        "nb_pools": 1,
        "params": {
            "--sr-disk": "auto",
        },
        "paths": ["tests/storage"],
        "markers": "quicktest and not sr_disk_4k",
        "name_filter": "not linstor and not zfsvol",
    },
    "linstor-main": {
        "description": "tests the linstor storage driver, but avoids migrations and reboots",
        "requirements": [
            "A pool with at least 3 hosts.",
            "An additional free disk on every host.",
            "A small VM that can be imported on the SR.",
        ],
        "nb_pools": 1,
        "params": {
            "--vm": "single/small_vm",
            "--sr-disk": "auto",
        },
        "paths": ["tests/storage/linstor"],
        "markers": "(small_vm or no_vm) and not reboot and not quicktest",
        "name_filter": "not migration",
    },
    "linstor-migrations": {
        "description": "tests migrations with the linstor storage driver",
        "requirements": [
            "A pool with at least 3 hosts.",
            "An additional free disk on every host.",
            "A second pool with at least 1 host and a SR to receive VMs.",
            "A small VM that can be imported on the SRs.",
        ],
        "nb_pools": 2,
        "params": {
            "--vm": "single/small_vm",
            "--sr-disk": "auto",
        },
        "paths": ["tests/storage/linstor"],
        "markers": "",
        "name_filter": "migration",
    },
    "linstor-reboots": {
        "description": "linstor storage driver tests that involve rebooting hosts",
        "requirements": [
            "A pool with at least 3 hosts, whose master host can be rebooted (best if reboots fast).",
            "An additional free disk on every host.",
            "A small VM that can be imported on the SRs.",
        ],
        "nb_pools": 1,
        "params": {
            "--vm": "single/small_vm",
            "--sr-disk": "auto",
        },
        "paths": ["tests/storage/linstor"],
        "markers": "reboot",
    },
    "linstor-quicktest": {
        "description": "runs `quicktest` on the linstor storage driver`",
        "requirements": [
            "A pool with at least 3 hosts.",
            "An additional free disk on every host.",
        ],
        "nb_pools": 1,
        "params": {
            "--sr-disk": "auto",
        },
        "paths": ["tests/storage/linstor"],
        "markers": "quicktest",
    },
    "largeblock-main": {
        "description": "tests the largeblock storage driver. avoids quicktest, migrations and reboots",
        "requirements": [
            "A pool with at least 1 host.",
            "An additional free 4KiB disk on the first host.",
            "A small VM that can be imported on the SRs.",
        ],
        "nb_pools": 1,
        "params": {
            "--vm": "single/small_vm",
            "--sr-disk-4k": "auto",
        },
        "paths": ["tests/storage"],
        "markers": "(small_vm or no_vm) and sr_disk_4k and not reboot and not quicktest",
        "name_filter": "not migration",
    },
    "largeblock-migrations": {
        "description": "a group of tests that need to run on hosts with 4KiB disks and migrates the VDI around",
        "requirements": [
            "A pool with at least 2 hosts, each with a local SR.",
            "An additional free 4KiB disk on the first host.",
            "A second pool with a SR to receive migrated VMs.",
            "A small VM that can be imported on the SRs.",
        ],
        "nb_pools": 2,
        "params": {
            "--vm": "single/small_vm",
            "--sr-disk-4k": "auto",
        },
        "paths": ["tests/storage"],
        "markers": "sr_disk_4k",
        "name_filter": "migration",
    },
    "largeblock-reboots": {
        "description": "largeblock storage driver tests that involve rebooting hosts",
        "requirements": [
            "A pool with at least 1 host.",
            "An additional free 4KiB disk on the first host.",
            "A small VM that can be imported on the SRs.",
        ],
        "nb_pools": 1,
        "params": {
            "--vm": "single/small_vm",
            "--sr-disk-4k": "auto",
        },
        "paths": ["tests/storage"],
        "markers": "sr_disk_4k and reboot",
    },
    "largeblock-quicktest": {
        "description": "runs `quicktest` on the largeblock storage driver",
        "requirements": [
            "A pool with at least 1 host",
            "An additional free 4KiB disk on the first host.",
        ],
        "nb_pools": 1,
        "params": {
            "--sr-disk-4k": "auto",
        },
        "paths": ["tests/storage"],
        "markers": "sr_disk_4k and quicktest",
    },
    "sb-main": {
        "description": "tests uefistored/varstored and SecureBoot using a small unix VM (or no VM when none needed)",
        "requirements": [
            "A pool >= 8.2.1. One host is enough.",
            "A fast-booting unix UEFI VM with efitools.",
            "See README.md for requirements on the test runner itself.",
        ],
        "nb_pools": 1,
        "params": {
            "--vm": "single/small_vm_efitools",
        },
        "paths": [
            "tests/uefi_sb/test_auth_var.py",
            "tests/uefi_sb/test_uefistored_sb.py",
            "tests/uefi_sb/test_varstored_sb.py",
            "tests/uefi_sb/test_sb_state.py"
        ],
        "markers": "not windows_vm",
    },
    "sb-certificates": {
        "description": "tests certificate propagation to disk by XAPI, and to VMs by uefistored/varstored",
        "requirements": [
            "A pool >= 8.2.1. On 8.3+, it needs at least two hosts. On 8.2, one is enough but more is better.",
            "On 8.3+ only, a second pool, single-host, available for temporarily joining the first pool"
            "and rebooting once ejected.",
            "A fast-booting unix UEFI VM with efitools.",
            "An additional free disk on the first host.",
        ],
        # nb_pools left to 1 so that the job can run on XCP-ng 8.2 with just one pool, but 2 are required in 8.3+
        "nb_pools": 1,
        "params": {
            "--sr-disk": "auto",
            "--vm": "single/small_vm_efitools",
        },
        "paths": ["tests/uefi_sb/test_uefistored_cert_flow.py", "tests/uefi_sb/test_varstored_cert_flow.py"],
    },
    "sb-windows": {
        "description": "tests uefistored/varstored and SecureBoot using a Windows VM",
        "requirements": [
            "A pool >= 8.2.1. One host is enough.",
            "A (small if possible) Windows UEFI VM.",
        ],
        "nb_pools": 1,
        "params": {
            "--vm": "single/small_vm_windows",
        },
        "paths": ["tests/uefi_sb"],
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
        "paths": ["tests/uefi_sb"],
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
        "paths": ["tests/uefi_sb"],
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
            "--vm": "single/small_vm_unix_tools",
        },
        "paths": ["tests/guest_tools/unix"],
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
        "paths": ["tests/guest_tools/unix"],
        "markers": "multi_vms",
    },
    "tools-windows": {
        "description": "tests our windows guest tools on a variety of VMs",
        "requirements": [
            "A pool >= 8.2. One host is enough.",
            "A variety of windows VMs supported by our tools installer.",
        ],
        "nb_pools": 1,
        "params": {
            "--vm[]": "multi/tools_windows",
        },
        "paths": ["tests/guest_tools/win"],
        "markers": "multi_vms",
    },
    "xen": {
        "description": "Testing of the Xen hypervisor itself",
        "requirements": [
            "A host with HVM FEP enabled (`hvm_fep` Xen command line parameter).",
            "A small VM that can be imported on the SRs.",
            "The host will be rebooted by the tests.",
        ],
        "nb_pools": 1,
        "params": {
            "--vm": "single/small_vm",
        },
        "paths": ["tests/xen"],
    },
    "vtpm": {
        "description": "Testing vTPM functionalities",
        "requirements": [
            "A XCP-ng host >= 8.3 and a Unix RPM-based or DEB-based UEFI VM with "
            "tpm2-tools installable from default repositories.",
        ],
        "nb_pools": 1,
        "params": {
            # The test also works on CentOS, for example, but in this job definition
            # we settle for a debian VM
            "--vm": "single/debian_uefi_vm",
        },
        "paths": ["tests/vtpm"],
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
    "xo": {
        "description": "tests that use Xen Orchestra, via xo-cli",
        "requirements": [
            "An XCP-ng host.",
            "xo-cli locally installed, in $PATH, and registered to an XO instance.",
        ],
        "nb_pools": 1,
        "params": {},
        "paths": ["tests/xo"],
    },
    "pci-passthrough": {
        "description": "Testing PCI passthrough functionalities",
        "requirements": [
            "A XCP-ng host >= 8.3 with a PGPU to passthrough.",
            "The host will be rebooted by the tests."
        ],
        "nb_pools": 1,
        "params": {},
        "paths": ["tests/pci_passthrough"],
    },
    "fs-diff": {
        "description": "Check for differences between 2 hosts file system",
        "requirements": [
            "2 XCP-ng host >= 8.2"
        ],
        # This test needs 2 hosts that can be from the same pool
        "nb_pools": 1,
        "params": {},
        "paths": ["tests/fs_diff"],
    },
    "pool-reboot": {
        "description": "Tests centered on pools with join/eject causing reboots",
        "requirements": [
            "1 XCP-ng pool and an additionnal host >= 8.2"
        ],
        "nb_pools": 2,
        "params": {},
        "paths": ["tests/misc/test_pool.py"],
    }
}

# List used by the 'check' action: tests listed here will not raise a check error
# if they are not selected by any test job.
# Adding a test to this list does not exclude it from test jobs. This is independent.
BROKEN_TESTS = [
    # not really broken but has complex prerequisites (3 NICs on 3 different networks)
    "tests/migration/test_host_evacuate.py::TestHostEvacuateWithNetwork",
    # running quicktest on zfsvol generates dangling TAP devices that are hard to
    # cleanup. Bug needs to be fixed before enabling quicktest on zfsvol.
    "tests/storage/zfsvol/test_zfsvol_sr.py::TestZfsvolVm::test_quicktest",
]

# Returns the vm filename or None if a host_version is passed and matches the one specified
# with the vm filename in vm_data.py. ex: ("centos6-32-hvm-created_8.2-zstd.xva", "8\.2\..*")
def filter_vm(vm, host_version):
    import re

    if type(vm) is tuple:
        if len(vm) != 2:
            print(f"ERROR: VM definition from vm_data.py is a tuple so it should contain exactly two items:\n{vm}")
            sys.exit(1)

        if host_version is None:
            print(f"ERROR: Host version required to filter VM definition:\n{vm}")
            print("\nFor some commands, you can specify the version with option --host-version.")
            sys.exit(1)

        # Keep the VM if versions match
        if re.match(vm[1], host_version):
            return vm[0]

        # Else discard
        return None

    return vm

def get_vm_or_vms_refs(handle, host_version=None):
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

    if type(VMS[category][key]) is list:
        # Multi VMs
        vms = list()
        for vm in VMS[category][key]:
            xva = filter_vm(vm, host_version)
            if xva is not None:
                vms.append(xva)
        if len(vms) == 0:
            vms = None
    else:
        # Single VMs
        vms = filter_vm(VMS[category][key], host_version)

    if vms is None:
        print(f"ERROR: Could not find VMS['{category}']['{key}'] for host version {host_version}.")
        print("You need to update your local vm_data.py.")
        print("You may also bypass this error by providing your own --vm parameter(s).")
        sys.exit(1)

    return vms

def build_pytest_cmd(job_data, hosts=None, host_version=None, pytest_args=[]):
    markers = job_data.get("markers", None)
    name_filter = job_data.get("name_filter", None)

    job_params = dict(job_data["params"])

    # Set/overwrite host_version with real host version if hosts are specified
    if hosts is not None:
        try:
            host = hosts.split(',')[0]
            cmd = ["lsb_release", "-sr"]
            host_version = ssh(host, cmd)
        except Exception as e:
            print(e, file=sys.stderr)

    def _join_pytest_args(arg, option):
        cli_args = []
        try:
            while True:
                i = pytest_args.index(option)
                value = pytest_args[i + 1]
                del pytest_args[i + 1]
                del pytest_args[i]
                cli_args.append(value)
        except ValueError:
            pass
        joined_cli_args = ") and (".join(cli_args)
        if arg and joined_cli_args:
            return f"({arg}) and ({joined_cli_args})"
        if joined_cli_args:
            return f"({joined_cli_args})"
        return arg

    # Merge name filter
    name_filter = _join_pytest_args(name_filter, "-k")

    # Merge markers
    markers = _join_pytest_args(markers, "-m")

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
            vms = get_vm_or_vms_refs(value, host_version)
            for vm_ref in vms:
                cmd.append(f"--vm={vm_ref}")
        elif key == "--vm":
            cmd.append(f"--vm={get_vm_or_vms_refs(value, host_version)}")
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
    cmd = build_pytest_cmd(JOBS[args.job], None, args.host_version, ["--collect-only"] + args.pytest_args)
    subprocess.run(cmd)

def action_check(args):
    error = False

    def extract_tests(cmd):
        tests = set()
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode != 0 and res.returncode != 5: # 5 means no test found
            print(f"""ERROR: Command failed {cmd}:
STDERR: {res.stderr.decode().strip()}
STDOUT: ---
{res.stdout.decode().strip()}
---""")
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
        job_tests |= extract_tests(build_pytest_cmd(job_data, None, None, ["--collect-only", "-q", "--vm=a_vm"]))
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
            job_tests |= extract_tests(build_pytest_cmd(job_data, None, None, ["--collect-only", "-q", "--vm=a_vm"]))
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
    cmd = build_pytest_cmd(JOBS[args.job], args.hosts, None, args.pytest_args)
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
    run_parser.add_argument("-v", "--host-version", help="host version to match VM filters.")
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
