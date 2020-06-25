## Test scripts for XCP-ng

Note: this is work in progress.

### Main requirements
* python >= 3.5
* pytest >= 5.4 (python3 version)
* xo-cli installed, in the PATH, and registered to an instance of XO that will be used during the tests

### Other requirements
* XCP-ng hosts that you can ssh to using a SSH key, non-interactively
* VM images suited to what the tests want. Some tests want a linux VM with SSH, available to import as an OVA over HTTP, for example.

On XCP-ng's test lab, the CI SSH private key allows to connect to any host installed for CI via PXE, and to any linux VM imported from pre-made images (OVA) and started.

### Configuration
The main configuration file is data.py. Copy data.py-dist to data.py and modify it if needed.

### Running tests
A crash-course about pytest will help you understanding how to start tests or groups of tests.

Examples:
```
pytest test_update_host.py --host=10.0.0.1
pytest test_cross_pool_live_storage_migration.py --hosts=10.0.0.1,10.0.0.2 --vm=mini-linux-x86_64-uefi
pytest test.py --host=10.0.0.1 --vm=mini-linux-x86_64-bios --vm=mini-linux-x86_64-uefi
```

Most tests take a `--host=yourtesthost` parameter if they need a single host to run on, or a `--hosts=host1,host2,host3` parameter if they need several hosts (e.g.: migration tests). The `--host` parameter can be specified several times. Then `pytest` will run the tests on each host, sequentially. Same with `--hosts` if you want to test several distinct groups of hosts in the same session.

Some tests accept an optional `--vm=OVA_URL|VM_key|IP_address` parameter. Those are tests that will import a VM before testing stuff on it:
* `OVA_URL` is an URL to download an OVA. It can also be a simple a filename, if your `data.py`'s `DEF_VM_URL` is correctly defined.
* `VM_key` refers to a key in `data.py`'s `VM_IMAGES` dict. Example: `mini-linux-x86_64-uefi`.
* `IP_address` allows to reuse an existing running VM, skipping the whole import, start, wait for VM to be up setup. Can be useful as a development tool. Some tests that accept `--vm` do not support it.
If `--vm` is not specified, defaults defined by the tests will be used. The `--vm` parameter can be specified several times. Then pytest will run several instances of the tests sequentially, one for each VM.

