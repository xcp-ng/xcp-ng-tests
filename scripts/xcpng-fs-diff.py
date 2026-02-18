#!/usr/bin/env python3

#
# Data structure format:
#
# The files to be checked are stored in Python dictionnaries with 4 main entries.
# - 'file' entry for standard files and the file md5 sum
# - 'file_symlink', valid symlinks to standard files and the file md5 sum
# - 'dir_symlink', valid symlinks to directory and the directory pointed at
# - 'broken_symlink', broken symlinks to directory and the file pointed at
#
# {
#   "files": {
#       "/opt/pbis/docs/pbis-open-installation-and-administration-guide.pdf": "7f9eaec58fd3422b79c88126cefdf503",
#       "/opt/pbis/docs/pbis-quick-start-guide-for-linux.pdf": "8bbd04e4c73cedefeeb35ed74a8a3d4b",
#       ...
#   },
#   "file_symlinks": {
#       "/opt/pbis/lib/libldap-2.4.so.2": "a417e1e86cec9735a76059b72d8e1cbf",
#       "/opt/pbis/lib/liblsaclient_ntlm.so.0": "9601bfc4827f0b9cff50cc591b2b6f11",
#       ...
#   },
#   "dir_symlinks": {
#       "/usr/tmp": "../var/tmp",
#       "/usr/lib/kbd/keymaps/legacy/ppc": "mac",
#       ...
#   },
#   "broken_symlinks": {
#       "/usr/lib/firmware/a300_pfp.fw": "qcom/a300_pfp.fw",
#       "/usr/lib/firmware/a300_pm4.fw": "qcom/a300_pm4.fw",
#       ...
#   },
#   "package": {
#       "kbd": "1.15.5",
#       "sudo": "1.8.23",
#       ...
#   }
# }
#

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
from enum import StrEnum, auto
from fnmatch import fnmatch

class DataType(StrEnum):
    FILE = auto()
    FILE_SYMLINK = auto()
    DIR_SYMLINK = auto()
    BROKEN_SYMLINK = auto()
    PACKAGE = auto()

def ignore_file(filename, ignored_files):
    for i in ignored_files:
        if fnmatch(filename, i):
            return True

    return False

def ssh_cmd(host, cmd):
    args = ["ssh", f"root@{host}", cmd]

    cmdres = subprocess.run(args, capture_output=True, text=True)
    if cmdres.returncode:
        raise Exception(cmdres.stderr)

    return cmdres.stdout

def ssh_get_files(host, file_type, folders):
    md5sum = False
    readlink = False
    folders = " ".join(folders)

    match file_type:
        case DataType.FILE:
            find_type = "-type f"
            md5sum = True
        case DataType.FILE_SYMLINK:
            find_type = "-type l -xtype f"
            md5sum = True
        case DataType.DIR_SYMLINK:
            find_type = "-type l -xtype d"
            readlink = True
        case DataType.BROKEN_SYMLINK:
            find_type = "-xtype l"
            readlink = True
        case _:
            print("Unknown file type: ", file=sys.stderr)
            return None

    find_cmd = f"find {folders} {find_type}"
    if readlink:
        find_cmd += " -exec readlink -n {} \\; -exec echo -n '  ' \\; -print"
    elif md5sum:
        # This will make one call to md5sum with all files passed as parameter
        # This is much more efficient than using find '-exec md5sum {}'
        find_cmd += " -print0 | xargs -0 md5sum"

    rawres = ssh_cmd(host, find_cmd)

    res = dict()
    for line in rawres.splitlines():
        entry = line.split(' ', 1)
        res[entry[1].strip()] = entry[0].strip()

    return res

def ssh_get_packages(host):
    packages = dict()

    res = ssh_cmd(host, "rpm -qa --queryformat '%{NAME} %{VERSION}\n'")
    for line in res.splitlines():
        entries = line.split(' ', 1)
        packages[entries[0]] = entries[1]

    return packages

def get_data(host, folders):
    ref_data = dict()

    try:
        ref_data[DataType.FILE] = ssh_get_files(host, DataType.FILE, folders)
        ref_data[DataType.FILE_SYMLINK] = ssh_get_files(host, DataType.FILE_SYMLINK, folders)
        ref_data[DataType.DIR_SYMLINK] = ssh_get_files(host, DataType.DIR_SYMLINK, folders)
        ref_data[DataType.BROKEN_SYMLINK] = ssh_get_files(host, DataType.BROKEN_SYMLINK, folders)
        ref_data[DataType.PACKAGE] = ssh_get_packages(host)
    except Exception as e:
        print(e, file=sys.stderr)
        exit(-1)

    return ref_data

def sftp_get(host, remote_file, local_file):
    opts = '-o "StrictHostKeyChecking no" -o "LogLevel ERROR" -o "UserKnownHostsFile /dev/null"'

    args = f"sftp {opts} -b - root@{host}"
    input = bytes(f"get {shlex.quote(remote_file)} {shlex.quote(local_file)}", 'utf-8')
    res = subprocess.run(
        args,
        input=input,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False
    )

    if res.returncode:
        raise Exception(f"Failed to get file from host: {res.returncode}")

    return res

def remote_diff(host_ref, host_test, filename):
    try:
        file_ref = None
        file_test = None

        # check remote files are text files
        cmd = f"file -b {shlex.quote(filename)}"
        file_type = ssh_cmd(host_ref, cmd)
        if not file_type.lower().startswith("ascii"):
            print("Binary file. Not showing diff")
            return

        fd, file_ref = tempfile.mkstemp(suffix='_ref')
        os.close(fd)
        sftp_get(host_ref, filename, file_ref)

        fd, file_test = tempfile.mkstemp(suffix='_test')
        os.close(fd)
        sftp_get(host_test, filename, file_test)

        args = ["diff", "-u", file_ref, file_test]
        diff_res = subprocess.run(args, capture_output=True, text=True)

        match diff_res.returncode:
            case 1:
                print(diff_res.stdout)
            case 2:
                raise Exception(diff_res.stderr)
            case _:
                pass

    except Exception as e:
        print(e, file=sys.stderr)
    finally:
        if file_ref is not None and os.path.exists(file_ref):
            os.remove(file_ref)
        if file_test is not None and os.path.exists(file_test):
            os.remove(file_test)

def print_results(results, show_diff, show_ignored):
    # Print what differs
    for dtype in DataType:
        if dtype == DataType.PACKAGE:
            for pkg, versions in results[dtype].items():
                print(f"{dtype} differs: {pkg} (ref={versions['ref']}, test={versions['test']})")
        else:
            for file in results[dtype]:
                print(f"{dtype} differs: {file}")
                if show_diff:
                    remote_diff(results['host']['ref'], results['host']['test'], file)

    # Print orphans
    for dtype in DataType:
        for file in results['orphan']['ref'][dtype]:
            print(f"{dtype} only exists on reference host: {file}")
        for file in results['orphan']['test'][dtype]:
            print(f"{dtype} only exists on tested host: {file}")

    if show_ignored and len(results['ignored_files']) > 0:
        print("\nIgnored files:")
        for f in results['ignored_files']:
            print(f"{f}")

def compare_data(ref, test, ignored_file_patterns):
    ref_data = ref['data']
    test_data = test['data']
    err = 0
    results = {
        'host': {
            'ref': ref['host'],
            'test': test['host']
        },
        DataType.FILE: [],
        DataType.FILE_SYMLINK: [],
        DataType.DIR_SYMLINK: [],
        DataType.BROKEN_SYMLINK: [],
        DataType.PACKAGE: {},
        'orphan': {
            'ref': {
                DataType.FILE: [],
                DataType.FILE_SYMLINK: [],
                DataType.DIR_SYMLINK: [],
                DataType.BROKEN_SYMLINK: [],
                DataType.PACKAGE: []
            },
            'test': {
                DataType.FILE: [],
                DataType.FILE_SYMLINK: [],
                DataType.DIR_SYMLINK: [],
                DataType.BROKEN_SYMLINK: [],
                DataType.PACKAGE: []
            }
        },
        'ignored_files': []
    }

    for dtype in test_data:
        for file in test_data[dtype]:
            if ignore_file(file, ignored_file_patterns):
                results['ignored_files'].append(file)
                continue

            if file not in ref_data[dtype]:
                results['orphan']['test'][dtype].append(file)
                err = 1
                continue

            if ref_data[dtype][file] != test_data[dtype][file]:
                err = 1
                if dtype == DataType.PACKAGE:
                    # Store package versions for PACKAGE data type
                    results[dtype][file] = {'ref': ref_data[dtype][file],
                                            'test': test_data[dtype][file]}
                else:
                    results[dtype].append(file)

            ref_data[dtype][file] = None

    # Check for files that only exist on the reference host
    for dtype in ref_data:
        for file, val in ref_data[dtype].items():
            if ignore_file(file, ignored_file_patterns):
                results['ignored_files'].append(file)
                continue

            if val is not None:
                err = 1
                results['orphan']['ref'][dtype].append(file)

    return results, err

# Load a previously saved json file containing reference data
def load_reference_files(filename):
    try:
        with open(filename, 'r') as fd:
            return json.load(fd)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        exit(-1)

# Save files from a reference host in json format
def save_reference_data(files, filename):
    try:
        with open(filename, 'w') as fd:
            json.dump(files, fd, indent=4)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        exit(-1)

def main():
    ref_data = None
    folders = ["/boot", "/etc", "/opt", "/usr"]
    ignored_file_patterns = [
        '/boot/initrd-*',
        '/boot/efi/*',
        '/boot/grub/*',
        '/boot/vmlinuz-fallback',
        '/boot/xen-fallback.gz',
        '/etc/adjtime',
        '/etc/chrony.conf',
        '/etc/fcoe/*',
        '/etc/firstboot.d/data/default-storage.conf',
        '/etc/firstboot.d/data/host.conf',
        '/etc/firstboot.d/data/iqn.conf',
        '/etc/firstboot.d/data/management.conf',
        '/etc/fstab',
        '/etc/group*',
        '/etc/grub.cfg',
        '/etc/grub-efi.cfg',
        '/etc/gshadow*',
        '/etc/hostname',
        '/etc/iscsi/initiatorname.iscsi',
        '/etc/issue',
        '/etc/krb5.conf',
        '/etc/ld.so.cache',
        '/etc/lvm/backup/*',
        '/etc/mtab',
        '/etc/machine-id',
        '/etc/nagios/nrpe.cfg',
        '/etc/nrpe.d/xs.cfg',
        '/etc/openldap/certs/*',
        '/etc/passwd*',
        '/etc/pki/ca-trust/extracted/java/cacerts',
        '/etc/pki/java/cacerts',
        '/etc/resolv.conf',
        '/etc/shadow*',
        '/etc/snmp/snmpd.xs.conf',
        '/etc/snmp/snmp.xs.conf',
        '/etc/ssh/ssh_host_*_key.pub',
        '/etc/ssh/ssh_host_*_key',
        '/etc/stunnel/*.pem',
        '/etc/stunnel/certs-pool/*.bak',
        '/etc/sysconfig/network',
        '/etc/sysconfig/network-scripts/interface-rename-data/*',
        '/etc/sysconfig/snmpd',
        '/etc/sysconfig/xencommons',
        '/etc/sysctl.d/91-net-ipv6.conf',
        '/etc/systemd/system/default.target.wants/test-pingpxe.service',
        '/etc/systemd/system/test-pingpxe.service',
        '/etc/vconsole.conf',
        '/etc/xsconsole/state.txt',
        '/etc/xensource-inventory',
        '/etc/xensource/boot_time_cpus',
        '/etc/xensource/ptoken',
        '/etc/xensource/xapi-pool-tls.bak',
        '/etc/xensource/xapi-pool-tls.pem',
        '/etc/xensource/xapi-ssl.pem',
        '/etc/xapi.d/plugins/__cache__/*.pyc',
        '/opt/xensource/gpg/trustdb.gpg',
        '/opt/xensource/sm/__pycache__/*.pyc',
        '/usr/lib64/xsconsole/__pycache__/*.pyc',
        '/usr/lib64/xsconsole/plugins-base/__pycache__/*.pyc',
        '/usr/local/sbin/test-pingpxe.sh',
    ]

    parser = argparse.ArgumentParser(description='Spot filesystem differences between 2 XCP-ng hosts')
    parser.add_argument('--reference-host', '-r', dest='ref_host',
                        help='The XCP-ng host used as reference')
    parser.add_argument('--test-host', '-t', dest='test_host',
                        help='The XCP-ng host to be tested after install or upgrade')
    parser.add_argument('--save-reference', '-s', dest='save_ref',
                        help='Save filesystem information of the reference host to a file')
    parser.add_argument('--load-reference', '-l', dest='load_ref',
                        help='Load reference filesystem information from a file')
    parser.add_argument('--show-diff', '-d', action='store_true', dest='show_diff',
                        help='Show diff of text files that differ. A reference host must be supplied with -r')
    parser.add_argument('--show-ignored', '-g', action='store_true', dest='show_ignored',
                        help='Show files that have been ignored')
    parser.add_argument('--add-folder', '-f', action='append', dest='folders', default=folders,
                        help='Add folders to the default searched folders (/boot, /etc, /opt, and /usr). '
                             'Can be specified multiple times')
    parser.add_argument('--ignore-file', '-i', action='append', dest='ignored_file_patterns',
                        default=ignored_file_patterns,
                        help='Add file patterns to the default ignored files. Can be specified multiple times')
    parser.add_argument('--json-output', '-j', action='store_true', dest='json_output',
                        help='Output results in json format')
    args = parser.parse_args(sys.argv[1:])

    if args.ref_host is None and args.show_diff:
        print("Missing parameters. -d must be used with -r. Try --help", file=sys.stderr)
        return -1

    if args.load_ref:
        if not args.json_output:
            print(f"Get reference data from {args.load_ref}")
        ref_data = load_reference_files(args.load_ref)
    elif args.ref_host:
        if not args.json_output:
            print(f"Get reference data from {args.ref_host}")
        ref_data = get_data(args.ref_host, args.folders)

        if args.save_ref:
            if not args.json_output:
                print(f"Saving reference data to {args.save_ref}")
            save_reference_data(ref_data, args.save_ref)

    if ref_data is None or args.test_host is None:
        if args.save_ref:
            return 0

        print("\nMissing parameters. Try --help", file=sys.stderr)
        return -1

    if not args.json_output:
        print(f"Get test host data from {args.test_host}")
    test_data = get_data(args.test_host, args.folders)

    ref = dict([('data', ref_data), ('host', args.ref_host)])
    test = dict([('data', test_data), ('host', args.test_host)])

    results, err = compare_data(ref, test, args.ignored_file_patterns)

    if args.json_output:
        if not args.show_ignored:
            results.pop('ignored_files')
        print(json.dumps(results, indent=2))
    else:
        if err != 0:
            print_results(results, args.show_diff, args.show_ignored)
        else:
            print("No difference found.")

    return err

if __name__ == '__main__':
    exit(main())
