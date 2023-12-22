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

def ignore_file(filename):
    ignored_files = [
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

    for i in ignored_files:
        if fnmatch(filename, i):
            return True

    return False

def ssh_cmd(host, cmd):
    args = ["ssh", "root@{}".format(host), cmd]

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

    find_cmd = "find {} {}".format(folders, find_type)
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

def get_files(host, folders):
    ref_files = dict()

    try:
        ref_files['file'] = ssh_get_files(host, FileType.FILE, folders)
        ref_files['file_symlink'] = ssh_get_files(host, FileType.FILE_SYMLINK, folders)
        ref_files['dir_symlink'] = ssh_get_files(host, FileType.DIR_SYMLINK, folders)
        ref_files['broken_symlink'] = ssh_get_files(host, FileType.BROKEN_SYMLINK, folders)
    except Exception as e:
        print(e, file=sys.stderr)
        exit(-1)

    return ref_files

def sftp_get(host, remote_file, local_file):
    opts = '-o "StrictHostKeyChecking no" -o "LogLevel ERROR" -o "UserKnownHostsFile /dev/null"'

    args = "sftp {} -b - root@{}".format(opts, host)
    input = bytes("get {} {}".format(shlex.quote(remote_file), shlex.quote(local_file)), 'utf-8')
    res = subprocess.run(
        args,
        input=input,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False
    )

    if res.returncode:
        raise Exception("Failed to get file from host: {}".format(res.returncode))

    return res

def remote_diff(host1, host2, filename):
    try:
        file1 = None
        file2 = None

        # check remote files are text files
        cmd = "file -b {}".format(shlex.quote(filename))
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

def compare_files(ref, test, show_diff):
    ref_files = ref['files']
    ref_host = ref['host']
    test_files = test['files']
    test_host = test['host']

    for ftype in test_files:
        for file in test_files[ftype]:
            if ignore_file(file):
                continue

            if file not in ref_files[ftype]:
                print("{} doesn't exist on reference host: {}".format(ftype, file))
                continue

            if ref_files[ftype][file] != test_files[ftype][file]:
                print("{} differs: {}".format(ftype, file))
                if show_diff:
                    remote_diff(ref_host, test_host, file)

            ref_files[ftype][file] = None

    # Check for files that only exist on the reference host
    for ftype in ref_files:
        for file, val in ref_files[ftype].items():
            if ignore_file(file):
                continue

            if val is not None:
                print("{} doesn't exist on tested host: {}".format(ftype, file))

# Load a previously saved json file containing a the reference files
def load_reference_files(filename):
    try:
        with open(filename, 'r') as fd:
            return json.load(fd)
    except Exception as e:
        print("Error: {}".format(e), file=sys.stderr)
        exit(-1)

# Save files from a reference host in json format
def save_reference_files(files, filename):
    try:
        with open(filename, 'w') as fd:
            json.dump(files, fd, indent=4)
    except Exception as e:
        print("Error: {}".format(e), file=sys.stderr)
        exit(-1)

def main():
    ref_files = None
    folders = ["/boot", "/etc", "/opt", "/usr"]

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
    args = parser.parse_args(sys.argv[1:])

    if args.ref_host is None and args.show_diff:
        print("Missing parameters. -d must be used with -r. Try --help", file=sys.stderr)
        return -1

    if args.load_ref:
        print("Get reference files from {}".format(args.load_ref))
        ref_files = load_reference_files(args.load_ref)
    elif args.ref_host:
        print("Get reference files from {}".format(args.ref_host))
        ref_files = get_files(args.ref_host, args.folders)

        if args.save_ref:
            print("Saving reference files to {}".format(args.save_ref))
            save_reference_files(ref_files, args.save_ref)

    if ref_files is None or args.test_host is None:
        if args.save_ref:
            return 0

        print("\nMissing parameters. Try --help", file=sys.stderr)
        return -1

    print("Get test host files from {}".format(args.test_host))
    test_files = get_files(args.test_host, args.folders)

    ref = dict([('files', ref_files), ('host', args.ref_host)])
    test = dict([('files', test_files), ('host', args.test_host)])

    print("\nResults:")
    compare_files(ref, test, args.show_diff)

    return 0

if __name__ == '__main__':
    exit(main())
