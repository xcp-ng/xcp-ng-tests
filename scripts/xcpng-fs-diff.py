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
import sys
import subprocess
import json
import tempfile
import os
import shlex
from fnmatch import fnmatch
from enum import Enum

class FileType(Enum):
    FILE = 0
    FILE_SYMLINK = 1
    DIR_SYMLINK = 2
    BROKEN_SYMLINK = 3

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
        case FileType.FILE:
            find_type = "-type f"
            md5sum = True
        case FileType.FILE_SYMLINK:
            find_type = "-type l -xtype f"
            md5sum = True
        case FileType.DIR_SYMLINK:
            find_type = "-type l -xtype d"
            readlink = True
        case FileType.BROKEN_SYMLINK:
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
        ref_data['file'] = ssh_get_files(host, FileType.FILE, folders)
        ref_data['file_symlink'] = ssh_get_files(host, FileType.FILE_SYMLINK, folders)
        ref_data['dir_symlink'] = ssh_get_files(host, FileType.DIR_SYMLINK, folders)
        ref_data['broken_symlink'] = ssh_get_files(host, FileType.BROKEN_SYMLINK, folders)
        ref_data['package'] = ssh_get_packages(host)
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

def remote_diff(host1, host2, filename):
    try:
        file1 = None
        file2 = None

        # check remote files are text files
        cmd = f"file -b {shlex.quote(filename)}"
        file_type = ssh_cmd(host1, cmd)
        if not file_type.lower().startswith("ascii"):
            print("Binary file. Not showing diff")
            return

        fd, file1 = tempfile.mkstemp()
        os.close(fd)
        sftp_get(host1, filename, file1)

        fd, file2 = tempfile.mkstemp()
        os.close(fd)
        sftp_get(host2, filename, file2)

        args = ["diff", "-u", file1, file2]
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
        if file1 is not None and os.path.exists(file1):
            os.remove(file1)
        if file2 is not None and os.path.exists(file2):
            os.remove(file2)

def compare_data(ref, test, ignored_file_patterns, show_diff):
    ref_data = ref['data']
    ref_host = ref['host']
    test_data = test['data']
    test_host = test['host']

    for dtype in test_data:
        for file in test_data[dtype]:
            if ignore_file(file, ignored_file_patterns):
                continue

            if file not in ref_data[dtype]:
                print(f"{dtype} doesn't exist on reference host: {file}")
                continue

            if ref_data[dtype][file] != test_data[dtype][file]:
                print(f"{dtype} differs: {file}", end='')
                if dtype == 'package':
                    print(f" (ref={ref_data[dtype][file]}, test={test_data[dtype][file]})")
                else:
                    print("")
                    if show_diff:
                        remote_diff(ref_host, test_host, file)

            ref_data[dtype][file] = None

    # Check for files that only exist on the reference host
    for dtype in ref_data:
        for file, val in ref_data[dtype].items():
            if ignore_file(file, ignored_file_patterns):
                continue

            if val is not None:
                print(f"{dtype} doesn't exist on tested host: {file}")

# Load a previously saved json file containing a the reference files
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
        '/boot/grub/*',
        '/boot/vmlinuz-fallback',
        '/boot/xen-fallback.gz',
        '/etc/chrony.conf',
        '/etc/firstboot.d/data/default-storage.conf',
        '/etc/firstboot.d/data/iqn.conf',
        '/etc/fstab',
        '/etc/group*',
        '/etc/grub.cfg',
        '/etc/gshadow*',
        '/etc/hostname',
        '/etc/iscsi/initiatorname.iscsi',
        '/etc/issue',
        '/etc/krb5.conf',
        '/etc/lvm/backup/*',
        '/etc/mtab',
        '/etc/machine-id',
        '/etc/passwd*',
        '/etc/pki/ca-trust/extracted/java/cacerts',
        '/etc/pki/java/cacerts',
        '/etc/shadow*',
        '/etc/ssh/ssh_host_*_key.pub',
        '/etc/ssh/ssh_host_*_key',
        '/etc/sysconfig/network',
        '/etc/sysconfig/network-scripts/interface-rename-data/*',
        '/etc/sysconfig/xencommons',
        '/etc/vconsole.conf',
        '/etc/xensource-inventory',
        '/etc/xensource/boot_time_cpus',
        '/etc/xensource/ptoken',
        '/etc/xensource/xapi-ssl.pem',
        '/opt/xensource/gpg/trustdb.gpg',
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
    parser.add_argument('--add-folder', '-f', action='append', dest='folders', default=folders,
                        help='Add folders to the default searched folders (/boot, /etc, /opt, and /usr). '
                             'Can be specified multiple times')
    parser.add_argument('--ignore-file', '-i', action='append', dest='ignored_file_patterns',
                        default=ignored_file_patterns,
                        help='Add file patterns to the default ignored files. Can be specified multiple times')
    args = parser.parse_args(sys.argv[1:])

    if args.ref_host is None and args.show_diff:
        print("Missing parameters. -d must be used with -r. Try --help", file=sys.stderr)
        return -1

    if args.load_ref:
        print(f"Get reference data from {args.load_ref}")
        ref_data = load_reference_files(args.load_ref)
    elif args.ref_host:
        print(f"Get reference data from {args.ref_host}")
        ref_data = get_data(args.ref_host, args.folders)

        if args.save_ref:
            print(f"Saving reference data to {args.save_ref}")
            save_reference_data(ref_data, args.save_ref)

    if ref_data is None or args.test_host is None:
        if args.save_ref:
            return 0

        print("\nMissing parameters. Try --help", file=sys.stderr)
        return -1

    print(f"Get test host data from {args.test_host}")
    test_data = get_data(args.test_host, args.folders)

    ref = dict([('data', ref_data), ('host', args.ref_host)])
    test = dict([('data', test_data), ('host', args.test_host)])

    print("\nResults:")
    compare_data(ref, test, args.ignored_file_patterns, args.show_diff)

    return 0

if __name__ == '__main__':
    exit(main())
