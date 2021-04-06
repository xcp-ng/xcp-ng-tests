# Test scripts for XCP-ng

Note: this is a work in progress.

## Main requirements
* python >= 3.5
* pytest >= 5.4 (python3 version)
* xo-cli installed, in the PATH, and registered to an instance of XO that will be used during the tests

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
