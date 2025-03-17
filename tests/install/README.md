# Automated XCP-ng installation tests

Note: this is a first iteration, which is bound to evolve a lot, when
we find the time.

Those tests cover installation, upgrade, and restore, using the
official installation ISO.  There is a small number of actual tests,
but a large number of parameters to apply the same testing steps to
various situations (UEFI or BIOS, choice of a release to upgrade from,
install purely from ISO or from a network pacakge repository, local SR
type to create).

## Terminology

In their current state, those tests can only install XCP-ng nested
inside a pre-existing XCP-ng pool.  In standard terminology of
hypervisor nesting, we have:

* L0: the pre-existing XCP-ng pool
* L1: the XCP-ng host(s) exercised by the tests
* L2: VMs launched by the hosts under test

As far as those install tests are concerned, L2 is out of scope, L0 is
simply refered to as "host" and L1 as "guest".

When it comes to running tests against our L1, the perspective is
shifting, with L1 being the "host", and L0 is then refered to as the
"nest" (which for the test itself is a hidden detail of the execution
environment).

## Prerequisites

L0 host must have
- a default SR (where the disk images for L1 hosts will be stored)
- one ISO SR (where the install images will be copied during install)

## Quick start

Use `data.py-dist` as usual as a reference to craft your `data.py`.
Especially important here are:
* `NETWORKS['MGMT']`, which should match the name of the network in
  your L0, which you want to use for the L1's management networks
* `TEST_SSH_PUBKEY`, a multiline string to be used as a
  `.ssh/authorized_keys` in the hosts to be installed (you must have
  access to one of the matching private keys)
* `ARP_SERVER`, a machine on the `NETWORKS['MGMT']` local network,
  with root access for one of the keys in `TEST_SSH_PUBKEY`, for use
  to determine the IP address of guests
* `TOOLS['iso-remaster']`, as local path to
  `scripts/iso-remaster/iso-remaster.sh` from the `xcp` repo
* `ISO_IMAGES_CACHE`, to specify where to cache the official ISO
  images
* `ISO_IMAGES_BASE`, to specify the base URL under which the images in
  `IMAGES_ISO` can be downloaded (defaults to the official XCP-ng
  public download server)
* `OBJECTS_NAME_PREFIX` (optional) will allow you to use a unique
  prefix to easily filter out *your* VMs in a shared lab

```
  XCPNG83_NIGHTLY=xcp-ng-8.3-ci-nightly-20250311.iso \
    pytest \
    --log-file-level=DEBUG --log-file=test-install.log \
    @tests/install/test-sequences/inst{,+upg,+upg+rst}.lst \
    --hosts=<L0-host-IP>
```

The above command instructs `pytest` to:
* run the test sequences as defined by the specified `.lst` files
  (which were specially written to chain an installation, an upgrade
  to same version, and a restore, all using a single nightly image)
* specify 
* to save detailed logs in a file, while during execution only
  high-level progress messages are shown to avoid flooding

Tests to be executed can always be listed by adding to `pytest`
options `--co -q`, which for the above command should show:

```
tests/install/test.py::TestNested::test_install[uefi-83nightly-iso-ext]
tests/install/test.py::TestNested::test_tune_firstboot[None-uefi-83nightly-host1-iso-ext]
tests/install/test.py::TestNested::test_boot_inst[uefi-83nightly-host1-iso-ext]
tests/install/test.py::TestNested::test_upgrade[uefi-83nightly-83nightly-host1-iso-ext]
tests/install/test.py::TestNested::test_boot_upg[uefi-83nightly-83nightly-host1-iso-ext]
tests/install/test.py::TestNested::test_restore[uefi-83nightly-83nightly-83nightly-iso-ext]
tests/install/test.py::TestNested::test_boot_rst[uefi-83nightly-83nightly-83nightly-iso-ext]
```

In order those are:
* running an XCP-ng installation in a UEFI guest, using a "8.3
  nightly" ISO (which when using the provided `data.py-dist` will take
  a path or URL to an ISO from envvar `XCPNG83_NIGHTLY`), using the
  ISO itself as RPM source, and creating an `EXT` local SR.  The ISO
  is first remastered to include an answerfile, as well as enable ssh
  access using the test key, and to shutdown instead of rebooting once
  installation is done.
* running a helper VM to modify the firstboot configuration set by the
  installer, so we can select hostname, and set unique UUIDs for our
  L1 host
* booting the installed host for the first time, check that XAPI
  properly starts up, that all firstboot services are starting OK, and
  that host is running the expected product version
* running an upgrade, similarly to install test, also specifying in
  parameters the version of the installed host we're upgrading
* booting the upgraded host for the first time, similarly to install test
* running a restore, similarly to install test, also specifying in
  parameters the version of the upgraded host we're upgrading
  ("version" which includes the version it was upgraded from, which
  still lives in the backup partition, and which is the one getting
  restored)
* booting the restored host for the first time, similarly to install test

Note: the selected management network configuration uses DHCP.

## Caching installs and chaining tests

Note: some of this section applies to tests that need to manipulate
the L0 host, but the cache concepts is also useful to those that just
mean to launch a test against an install host from the cache.  This
section can possibly been split to separate those concerns.

To allow launching all those install steps one by one, and applying
tests on the resulting L1 hosts, the state of those hosts are cached
in L0 as a clone of the L1 (seen from L0 as a guest).

The test-chaining mechanism uses the `name-description` field to
identify the output of a given test, for example `[Cache for
install.test::Nested::upgrade[uefi-83nightly-83nightly-host1-iso-ext]-vm1-1857a3f0ef69640d10348e9f0adef09f6e9a7c5d]`.
This includes a the shortened test name with its full test arguments,
the ID of the L1 "host VM" (as a test can launch more than one), and
the git revision of the test repo.

Currently the tests producing the image necessary for a given test are
recorded using `pytest-dependencies`.  As a consequence, when running
a test that needs the image produced by an earlier test, the
`--ignore-unknown-dependency` flag must be used, or the test will be
skipped, as in:
```
$ XCPNG83_NIGHTLY=~/iso/xcp-ng-8.3.0.iso pytest --hosts=172.16.210.11 tests/install/test.py::TestNested::test_boot_inst[uefi-83nightly-host1-iso-ext]
...
SKIPPED (test_boot_inst[uefi-83nightly-host1-iso-ext] depends on TestNested::test_tune_firstboot[None-uefi-83nightly-host1-iso-ext])
```

This git revision ensures consistency of the test runs, avoiding
during test development the inadvertent use of the output of an
outdated version of a given test.  It thus creates a strong
constraint, that all changes be committed before the test is launched.
As a consequence when working on a test that needs a VM from the
cache, as we don't want to rerun all the preceding tests, it requires
an explicit waiver to use an image with a different revision; this is
done in `data.py` in a dict specifying an equivalent cache ID to be
used when one is not found:

IMAGE_EQUIVS = {
    'install.test::Nested::upgrade[bios-75-821.1-host1-iso-nosr]-vm1-6ab478747815979b22e0d0555aa2782bf33850ed':
    'install.test::Nested::upgrade[bios-75-821.1-host1-iso-nosr]-vm1-17ba75d798499b563bfadca98f8d22a2cb81efdc',
}

Note: the git revision is read at the start of the test run, so you
can safely go on working with your codebase while you have a test
running.

If you're not sure of the cache IDs to put in there:
* attempt to run your test, it will disclose the missing cache ID, you
  can paste it as a key in `IMAGE_EQUIVS`:
  ```
  Mar 17 14:53:51.599 INFO Could not find a VM in cache for 'install.test::Nested::tune_firstboot[None-uefi-83nightly-host1-iso-ext]-vm1-ce73023e06d680355dbfb0b726aae8eeee0c07ff'
  Mar 17 14:53:51.600 ERROR exception caught...
  ```
* for the value, use the same string and just replace the git revision
  with the one you want to use, which must exist in your cache.
  Depending on the situation, you may find the revision using `git
  log` or `git revlog`, or possibly list the ones available in your
  cache with something like:
  ```
  xe vm-list params=name-description | grep -F 'install.test::Nested::upgrade[bios-75-821.1-host1-iso-nosr]-vm1-'
  ```

Note: this mechanism can surely be improved, suggestions welcomed.
Note that a pending PR introduces the notion of "default git revision
to try for a given list of images".

## Running classical XCP-ng tests

Running classical XCP-ng tests, which in our case are meant to run
against the L1 host, uses a specific syntax for the `--hosts` flag,
along with the `--nest=<L0-host>` flag, as in:

```
pytest --nest=172.16.210.11 \
  --hosts=cache://install.test::Nested::boot_inst[uefi-83nightly-host1-iso-ext]-vm1-1857a3f0ef69640d10348e9f0adef09f6e9a7c5d \
  tests/misc/test_basic_without_ssh.py::test_vm_start_stop
```
