# Test scripts for XCP-ng

Note: this is a work in progress.

## Main requirements
* python >= 3.5
* pytest >= 5.4 (python3 version)
* xo-cli installed, in the PATH, and registered to an instance of XO that will be used during the tests

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

On XCP-ng's test lab, the CI SSH private key allows to connect to any host installed for CI via PXE, and to any linux VM imported from pre-made images (OVA) and started.

For Guest UEFI Secure Boot tests, the requirements are:
* Test Runner
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

## Configuration
The main configuration file is data.py. Copy data.py-dist to data.py and modify it if needed.

## Running tests
A crash-course about pytest will help you understanding how to start tests or groups of tests.

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

### Test log level

Using `pytest` you can choose to change the log level that appears in your console with the [`--log-cli-level` option](https://docs.pytest.org/en/latest/how-to/logging.html#live-logs).

To log to a file you can use `--log-file` option and choose the level with `--log-file-level`.

More info about pytest logging available here: https://docs.pytest.org/en/latest/how-to/logging.html.

## VM setup
Many tests expect VMs with:
* OpenSSH server installed and accepting pubkey authentication for the `root` user
* Guest tools installed so that the VM reports its IP address, can be shutdown cleanly, and migrated without issues

Here are instructions that should help creating such VMs.

### Linux

* Eject installation ISO
* Setup network
* Install openssh-server and enable it
* Install guest tools, then eject guest tools ISO
* Add XCP-ng's CI public key in /root/.ssh/authorized_keys (mode 0600)
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

# Automating VM Setup with Ansible

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

## The Ansible Playbook

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
