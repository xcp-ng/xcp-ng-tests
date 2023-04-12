# Test scripts for XCP-ng

Note: this is a perpertual work in progress. If you encounter any obstacles or bugs, let us know!

## Main requirements
* python >= 3.5
* pytest >= 5.4 (python3 version)
* xo-cli >= 0.17.0 installed, in the PATH, and registered to an instance of XO that will be used during the tests

### Quick install (python requirements)

Install the python requirements using pip:

```
$ pip install -r requirements/base.txt

```

Additionally, for dev dependencies (things like the linter / style checker):

```
$ pip install -r requirements/dev.txt

```

## Other requirements
* XCP-ng hosts that you can ssh to using a SSH key, non-interactively
* VM images suited to what the tests want. Some tests want a linux VM with SSH, available to import as an OVA over HTTP, for example.

On XCP-ng's test lab, the CI SSH private key allows to connect to any host installed for CI via PXE, and to any linux VM imported from pre-made images (OVA).

For Guest UEFI Secure Boot tests, the requirements are:
* Test Runner (where pytest will be executed)
    * `sbsign` or `pesign`
    * If using `pesign`, `certutil` and `pk12util` must also be installed.
      These should be included as package dependencies for your distro.
    * `openssl`
* VM
    * `chattr`
    * `efitools` for uefistored auth var tests
    * `util-linux` for uefistored auth var tests in Alpine VMs
* XCP-ng Host (installed by default on XCP-ng 8.2+)
    * `uefistored`
    * `varstored-tools`

Many tests have specific requirements, detailed in a comment at the top of the test file: minimal number of hosts in a pool, number of pools, VMs with specific characteristics (OS, BIOS vs UEFI, additional tools installed in the VM, additional networks in the pool, presence of an unused disk on one host or every host...). Markers, jobs defined in `jobs.py` (`./jobs.py show JOBNAME` will display the requirements and the reference to a VM or VM group), VMs and VM groups defined in `vm-data.py-dist` may all help understanding what tests can run with what VMs.

## Configuration
The main configuration file is data.py. Copy data.py-dist to data.py and modify it if needed.

## Running tests

Refer to pytest's documentation or tutorials to understand how to start tests or groups of tests.

Examples:
```
pytest tests/storage/ext/test_ext_sr.py --hosts=10.0.0.1
pytest tests/storage/zfs/test_zfs_sr_crosspool_migration.py --hosts=10.0.0.1,10.0.0.2 --vm=mini-linux-x86_64-uefi
pytest tests/misc/test_vm_basic_operations.py --hosts=10.0.0.1 --vm=mini-linux-x86_64-bios --vm=mini-linux-x86_64-uefi
```

Most tests take a `--hosts=yourtesthost` (or `--hosts=host1,host2,...` if they need several pools, e.g. crosspool migration tests).
The `--hosts` parameter can be specified several times. Then `pytest` will run the tests on each host or group of hosts, sequentially.

When a test requires a single pool of several hosts, only mention the master host in the `--hosts` option.

Some tests accept an optional `--vm=OVA_URL|VM_key|IP_address` parameter. Those are tests that will import a VM before testing stuff on it:
* `OVA_URL` is an URL to download an OVA. It can also be a simple a filename, if your `data.py`'s `DEF_VM_URL` is correctly defined.
* `VM_key` refers to a key in `data.py`'s `VM_IMAGES` dict. Example: `mini-linux-x86_64-uefi`.
* `IP_address` allows to reuse an existing running VM, skipping the whole import, start, wait for VM to be up setup. Can be useful as a development tool. Some tests that accept `--vm` do not support it.
If `--vm` is not specified, defaults defined by the tests will be used.
The `--vm` parameter can be specified several times. Then pytest will run several instances of the tests sequentially, one for each VM.

See also, below: "Markers and test selection" and "Running test jobs with `jobs.py`"

### Test log level

Using `pytest` you can choose to change the log level that appears in your console with the [`--log-cli-level` option](https://docs.pytest.org/en/latest/how-to/logging.html#live-logs).

To log to a file you can use `--log-file` option and choose the level with `--log-file-level`.

More info about pytest logging available here: https://docs.pytest.org/en/latest/how-to/logging.html.

See also, below: "Markers and test selection" and "Running test jobs with `jobs.py`"

### Markers and test selection

pytest allows the use of markers to select tests, using the `-m` switch.

Refer to the marker definitions in our `pytest.ini`.

We defined various markers, that currently belong to the following conceptual families of markers:
* Descriptive markers:
  * Automatically added based on the fixtures required by the tests: do they require a VM? Unix? Windows? UEFI? Do they need a free disk that they can format? Do they require a second host in the first pool? A second pool? /!\ Not all fixtures are automatically translated into markers! (this is handled by the `pytest_collection_modifyitems` hook in `conftest.py`)
  * Manually added to the tests by developers for easier test filtering: does the test reboot a host? Is it a flaky test? Does it have complex prerequisites?
* Target markers, manually added, that hint about what kind of configuration is best appropriate with a given test:
  * Tests that should be preferrably just run with a small and fast-booting VM, for faster execution.
  * Tests that should be run on the largest variety of VMs.
  * Tests that should be run at least once with a very big VM.
* Markers used in the tests themselves to change their behaviour. Those won't be very useful to select tests with `-m`. We're just mentioning them for the sake of completeness.

Here's an example of selection we can do thanks to the markers:

```
# Run storage driver tests that either need no VM at all, or advise to use a small VM. Exclude tests that reboot the hosts.
pytest tests/storage -m "(small_vm or no_vm) and not reboot" --hosts=ip_of_poolmaster1,ip_of_poolmaster2 --vm=http://path/to/a_small_vm.xva --sr-disk=auto
```

Another example:

```
# Run secure boot tests that require a Unix VM (as opposed to a Windows VM) and that should ideally be run on a large variety of VMs
pytest tests/uefistored -m "multi_vms and unix_vm" --hosts=ip_of_poolmaster --vm=http://path/to/unix_vm_1.xva --vm=http://path/to/unix_vm_2.xva --vm=http://path/to/unix_vm_3.xva
```


The `-k` option may also be used to select tests, but this time based on their name. To be used with caution as this can be a fragile filter.

TIP: Use pytest's `--collect-only` parameter to list the tests and check the effect of your selection, before actually running them.

Options `-m` and `-k` are heavily used in `jobs.py`.

### Running test jobs with `jobs.py`

`jobs.py` is a script that was primarily developed to define test jobs that we can run in our test automation. There are various constraints to take into account. Mainly: test prerequisites (pools and VMs), test duration (some tests can be very long), and also whether the tests will make the pool temporarily unavailable (tests that reboot), or whether the tests are flaky (usually pass, but sometimes fail for a reason difficult to avoid or fix).

We wanted the job definitions to be in this git repository, that's why the job definitions are in the `jobs.py` file itself (plus `vm_data.py` for VM selection).

To use `./jobs.py`, you also need to populate `vm_data.py` to define the VM groups that are necessary to run jobs (unless `--vm` is provided on the command line to override the defaults).

The output of commands below is given as example and may not reflect the current state of the jobs definitions.

#### List jobs
```
$ ./jobs.py list
main: a group of not-too-long tests that run either without a VM, or with a single small one
main-multi: a group of tests that need to run on the largest variety of VMs
quicktest: XAPI's quicktest, not so quick by the way
storage-main: tests all storage drivers (except linstor), but avoids migrations and reboots
storage-migrations: tests migrations with all storage drivers (except linstor)
storage-reboots: storage driver tests that involve rebooting hosts (except linstor and flaky tests)
sb-main: tests uefistored and SecureBoot using a small unix VM (or no VM when none needed)
sb-windows: tests uefistored and SecureBoot using a Windows VM
sb-unix-multi: checks basic Secure-Boot support on a variety of Unix VMs
sb-windows-multi: checks basic Secure-Boot support on a variety of Windows VMs
tools-unix: tests our unix guest tools on a single small VM
tools-unix-multi: tests our unix guest tools on a variety of VMs
flaky: tests that usually pass, but sometimes fail unexpectedly
```

#### Display information about a job
```
$ ./jobs.py show sb-unix-multi
{
    "description": "checks basic Secure-Boot support on a variety of Unix VMs",
    "requirements": [
        "A pool >= 8.2.1. One host is enough.",
        "A variety of UEFI Unix VMs.",
        "See README.md for requirements on the test runner itself."
    ],
    "nb_pools": 1,
    "params": {
        "--vm[]": "multi/uefi_unix"
    },
    "paths": [
        "tests/uefistored"
    ],
    "markers": "multi_vms and unix_vm"
}
```

Here you get the requirements for the job and the test selection (`paths` and optionnaly `markers` and/or `name_filter`).

A very important information is also the `--vm` (single VM) or `--vm[]` (multiple VMs) parameter. The value is the key of a list of VMs that must be defined in `vm_data.py`, or the job won't execute (actually, you can still execute the job by passing one or more `--vm` parameters manually). Check the example `vm_data.py-dist` file. Inside XCP-ng's testing lab, a ready to use `vm_data.py` is available that lists the VMs available in the lab.

#### Display more information about a job
There are two more commands that you can use to display information about a job:

```
$ ./jobs.py collect sb-unix-multi
[...]
collected 175 items / 170 deselected / 5 selected

<Package uefistored>
  <Module test_secure_boot.py>
    <Class TestGuestLinuxUEFISecureBoot>
      <Function test_boot_success_when_pool_db_set_and_images_signed[hosts0-http://path/to/vm1.xva]>
      <Function test_boot_success_when_pool_db_set_and_images_signed[hosts0-http://path/to/vm2.xva]>
      <Function test_boot_success_when_pool_db_set_and_images_signed[hosts0-http://path/to/vm3.xva]>
```

This lists the tests that are selected by the job. Tests may be repeated if they will run several times, as in the case of this example because there are 3 VMs to test. I chose a job whose output is small for the sake of documentation conciseness, but the output can be a lot bigger!

Lastly, the `run` command with the `--print-only` switch will display the command it would execute, but not execute it.

```
# job with default parameters
$ ./jobs.py run --print-only sb-unix-multi ip_of_poolmaster
pytest tests/uefistored -m "multi_vms and unix_vm" --hosts=ip_of_poolmaster --vm=http://path/to/vm1.xva --vm=http://path/to/vm2.xva --vm=http://path/to/vm3.xva

# same, but we override the list of VMs
$ ./jobs.py run --print-only sb-unix-multi ip_of_poolmaster --vm=http://path/to/vm4.xva
pytest tests/uefistored -m "multi_vms and unix_vm" --hosts=ip_of_poolmaster --vm=http://path/to/vm4.xva
```

#### Run a job
```
usage: jobs.py run [-h] [--print-only] job hosts ...

positional arguments:
  job               name of the job to run.
  hosts             master host(s) of pools to run the tests on, comma-separated.
  pytest_args       all additional arguments after the last positional argument will be passed to pytest and replace default job params if needed.

optional arguments:
  -h, --help        show this help message and exit
  --print-only, -p  print the command, but don't run it. Must be specified before positional arguments.
```

Example:
```
# job with default parameters
$ ./jobs.py run sb-unix-multi ip_of_poolmaster
pytest tests/uefistored -m "multi_vms and unix_vm" --hosts=ip_of_poolmaster --vm=http://path/to/vm1.xva --vm=http://path/to/vm2.xva --vm=http://path/to/vm3.xva
[... job executes...]
```

Any parameter added at the end of the command will be passed to `pytest`. Any parameter added that is already defined in the job's "params" (see output of `./jobs.py show`) will replace it, and `--vm` also replaces `--vm[]` in the case of jobs designed to run tests on multiple VMs.

```
# same, but we override the list of VMs
$ ./jobs.py run --print-only sb-unix-multi ip_of_poolmaster --vm=http://path/to/vm4.xva
pytest tests/uefistored -m "multi_vms and unix_vm" --hosts=ip_of_poolmaster --vm=http://path/to/vm4.xva
[... job executes...]
```


#### Check job consistency
`./jobs.py check` will attempt to check whether the jobs are consistent. For example: are all tests defined in this repository selected in at least one job?

It is automatically executed after every pull request or commit pushed to this repository.

#### More
Check `./jobs.py --help` and `./jobs.py {command name} --help`.

## VM setup
Many tests expect VMs with:
* OpenSSH server installed and accepting pubkey authentication for the `root` user
* Guest tools installed so that the VM reports its IP address, can be shutdown cleanly, and migrated without issues
* Other common prerequisites detailed below.

Here are instructions that should help creating such VMs.

### Linux and other unixes

* Eject installation ISO
* Setup network
* Install openssh-server and enable it
* Install bash in order to ensure a common shell is available in all test VMs
* Install guest tools, then eject guest tools ISO
* Add a SSH public key in /root/.ssh/authorized_keys (mode 0600) so that tests may run SSH commands inside the VMs
  * For XCP-ng's test lab, add XCP-ng's CI public key
* Test you can ssh to it
* Reboot, check everything is fine
* Poweroff

Special cases:
* CentOS 6:
  * requires this: `restorecon -r /root/.ssh`
  * comment out HWADDR in `/etc/sysconfig/network-scripts/ifcfg-eth0` else any copy of this VM will refuse to start the network on eth0 because the MAC address changed
* Alpine (for uefistored auth var tests):
  * Install `util-linux`:
    ```
    apk update
    apk add --upgrade util-linux
    ```

### Windows 10

* Create user root
* Eject installation ISO
* Install OpenSSH Server: Apps & Features > Optional features > Add a feature > Open SSH server
* Enable it: https://docs.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_firstuse#initial-configuration-of-ssh-server
```
Start-Service sshd
Set-Service -Name sshd -StartupType 'Automatic'
# Confirm the Firewall rule is configured. It should be created automatically by setup.
Get-NetFirewallRule -Name *ssh*
# There should be a firewall rule named "OpenSSH-Server-In-TCP", which should be enabled
# If the firewall does not exist, create one
New-NetFirewallRule -Name sshd -DisplayName 'OpenSSH Server (sshd)' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22
```
* Try to login as root
* Install git for windows (includes git-bash)
* Make bash the default shell for OpenSSH sessions: https://docs.microsoft.com/en-us/windows-server/administration/openssh/openssh_server_configuration
```
New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name DefaultShell -Value "C:\Program Files\Git\bin\bash.exe" -PropertyType String -Force
```
* Allow pubkey authentication with XCP-ng's CI key
  * From linux: `ssh-copy-id -i ~/.ssh/id_rsa_xcpngci.pub root@{VM IP address}`
  * Then fix the rights of C:\Users\root\.ssh\authorized_keys:
    * Properties > Security > Advanced > Disabled inheritance > Convert inherited permissions > Leave only exactly two entries: SYSTEM and root.
  * Also copy the authorized_keys file to C:\ProgramData\ssh\administrators_authorized_keys
  * Fix its rights: leave only Administrators and SYSTEM.
* Check ssh to the VM works without asking for a password and opens bash as expected
* Reboot, check it's still OK
* Poweroff

## Automating VM Setup with Ansible

There is an Ansible runner that performs automatic updates on VMs using Ansible
playbooks. The runner then exports the updated VMs as XVAs. The runner is found
at `scripts/ansible/runner.py`.

```bash
usage: runner.py [-h] [--http HTTP] [--print-images] [--host HOST]
                 [--export-directory EXPORT_DIRECTORY]
                 playbook

Run a playbook for updating test VMs

positional arguments:
  playbook              The Ansible playbook.

optional arguments:
  -h, --help            show this help message and exit
  --http HTTP           The HTTP/PXE server containing the test VMs. Defaults
                        to DEF_VM_URL in data.py, if not found then defaults
                        to http://pxe/images/
  --print-images        Show the available images on the PXE server and then
                        exit.
  --host HOST, -x HOST  The XCP-ng hostname or IP address. Defaults to host
                        found in data.py
  --export-directory EXPORT_DIRECTORY, -e EXPORT_DIRECTORY
                        The directory on the XCP-ng host to export the updated
                        image. Defaults to /root/ansible-updates/
```

### The Ansible Playbook

The Ansible playbook must have a host name that matches the file name of an
XVA located at the http server found using the `DEF_VM_URL` variable in
data.py.

Ansible host names do not support hyphens, so they are converted to underscores.

For example, `alpine-uefi-minimal-3.12.0.xva` becomes `alpine_uefi_minimal_3.12.0.xva`.

The runner then spins a VM using that XVA, applys the Ansible playbook, then
exports the VM to a new XVA.

As an example, given `DEF_VM_URL = "http://pxe/images/"` in `data.py` and
the following playbook, the runner will look for an XVA file at
`http://pxe/images/alpine-uefi-minimal-3.12.0.xva` and update it as described.

Note that the "hosts" in Ansible refers to the VMs, not the XCP-ng host.
The XCP-ng host used to run the VM is picked randomly from the HOSTS variable
in data.py.

```yaml
---
- hosts: alpine_uefi_minimal_3.12.0.xva
  remote_user: root
  gather_facts: no
  tasks:
    - name: Install Python for Ansible
      raw: test -f /usr/bin/python3 || apk add --update --no-cache python3

    - name: Install util-linux and efitools
      community.general.apk:
        name: util-linux efitools
```

## install_xcpng.py

This script installs, upgrades or restores XCP-ng in a VM using a PXE server whose configuration can be defined dynamically. Basically, it writes files in a directory named after the MAC address of the VM, on a PXE server that will then build a boot configuration for the given MAC address. This is rather specific to Vates' test lab at the moment. If you are interested in automated installation in general, check https://xcp-ng.org/docs/install.html#automated-install.

```
usage: install_xcpng.py [-h] [--installer INSTALLER] host vm_uuid action xcpng_version
```

Installation example:
```
python scripts/test_install_xcpng.py 10.0.0.2 3bdf2cc6-4e6e-526d-4f18-bf7899953af6 install 8.2.1 --installer=https://mirrors.xcp-ng.org/netinstall/8.2.1/
```

Upgrade example:
```
python scripts/test_install_xcpng.py 10.0.0.2 f0f5f010-80c6-25ae-44a2-1fb154e32d14 upgrade 8.2.1
```

Restore example:
```
python scripts/test_install_xcpng.py 10.0.0.2 f0f5f010-80c6-25ae-44a2-1fb154e32d14 restore 8.2.1
```
Note: in case of restore, the version must be that of the installer (here 8.2.1), not the version of XCP-ng that will be restored.

The script requires the addressable name or IP of the PXE config server to be defined in `data.py`:
```
# PXE config server for automated XCP-ng installation
PXE_CONFIG_SERVER = 'pxe'
```

The `installer` parameter is optional. If you leave it empty it will be automatically defined as `http://<PXE_CONFIG_SERVER>/installers/xcp-ng/<version>/`.

## Bash scripts

 * get_xva_bridge.sh: a script to get the XAPI bridge value from inside a xva file and the compression method used for this xva file.

```
$ /path/to/get_xva_bridge.sh alpine-minimal-3.12.0.xva
ova.xml
alpine-minimal-3.12.0.xva's bridge network is: xapi1 and its compression method is: tar.
```

 * set_xva_bridge.sh: a script to modify the XAPI bridge value inside a xva file and the compression method used for this xva file if wanted. The original xva file is saved before modification.

```
- Usage: /path/to/set_xva_bridge.sh [XVA_filename] compression[zstd|gzip] bridge_value[xenbr0|xapi[:9]|...]
- All options are mandatory.

$ /path/to/set_xva_bridge.sh alpine-minimal-3.12.0.xva zstd xenbr0
```
