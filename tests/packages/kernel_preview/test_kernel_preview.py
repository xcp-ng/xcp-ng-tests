from __future__ import annotations

import pytest

import logging
import os
import re
from dataclasses import dataclass, field

from lib.host import Host

# Requirements:
# From --hosts parameter:
# - host(A1): a disposable XCP-ng 8.3 host (or nested VM) with access to the XCP-ng RPM repositories,
#   including the preview repository (--kernelpreview-repo, default xcp-ng-kernelpreview).
#   The test updates, reboots and downgrades the host's kernel.
#
# Scenario:
# 1. yum update from the main channel (kernelpreview_host fixture)
# 2. yum update with the preview repository enabled, reboot, check the running kernel is the preview one
# 3. load/unload every out-of-tree module (default and -alt packages) built for the preview kernel
# 4. downgrade the kernel and its modules back to the main channel versions, reboot, check the kernel
# 5. load/unload every out-of-tree module (default and -alt packages) built for the main kernel
#
# Modules already loaded when a module phase starts (e.g. the drivers the host is running on) are never
# loaded nor unloaded.

MAIN_REPOS = ['xcp-ng-base', 'xcp-ng-updates']

# Modules that are known not to load in the test environment: module name -> reason
KNOWN_LOAD_FAILURES: dict[str, str] = {}

def _lines(output: str) -> list[str]:
    return [line.strip() for line in output.splitlines() if line.strip()]

def _module_name(path: str) -> str:
    return re.sub(r'\.ko(\.xz)?$', '', os.path.basename(path)).replace('-', '_')

def _ko_re(uname: str) -> re.Pattern[str]:
    return re.compile(rf'^/lib/modules/{re.escape(uname)}/.*\.ko(\.xz)?$')

def repo_kernel_uname(host: Host, repo_args: str) -> str:
    """kernel-uname-r provided by the latest kernel package available in the given repositories."""
    provides = _lines(host.ssh(f"repoquery {repo_args} --provides kernel"))
    unames = sorted({line.split('=', 1)[1].strip() for line in provides if line.startswith('kernel-uname-r')})
    assert len(unames) == 1, f"Expected exactly one kernel-uname-r for 'kernel' ({repo_args}), got {unames}"
    return unames[0]

def kernel_nvr(host: Host, repo_args: str) -> str:
    nvrs = _lines(host.ssh(f"repoquery {repo_args} --qf '%{{name}}-%{{version}}-%{{release}}' kernel"))
    assert len(nvrs) == 1, f"Expected exactly one kernel package ({repo_args}), got {nvrs}"
    return nvrs[0]

def packages_requiring_kernel(host: Host, uname: str, repo_args: str) -> list[str]:
    out = host.ssh(f"repoquery {repo_args} --qf '%{{name}}' --whatrequires 'kernel-uname-r = {uname}'")
    return sorted(set(_lines(out)) - {'kernel'})

def kernel_srpm_packages(host: Host, srpm: str) -> list[str]:
    """Installed packages, other than kernel itself, built from the given source RPM."""
    out = host.ssh("rpm -qa --qf '%{NAME} %{SOURCERPM}\\n'")
    return sorted(name for name, src in (line.split() for line in _lines(out)) if src == srpm and name != 'kernel')

def installed_package_names(host: Host) -> set[str]:
    return set(_lines(host.ssh("rpm -qa --qf '%{NAME}\\n'")))

def package_files(host: Host, pkg: str, repo_args: str | None) -> list[str]:
    """Files of an installed package (repo_args is None) or of a package available in the repositories."""
    if repo_args is None:
        return _lines(host.ssh(f'rpm -ql {pkg}'))
    return _lines(host.ssh(f'repoquery {repo_args} -l {pkg}'))

def package_modules(host: Host, pkg: str, uname: str, repo_args: str | None) -> dict[str, str]:
    """Module name -> .ko path, for the modules the package ships for the given kernel."""
    ko = _ko_re(uname)
    return {_module_name(f): f for f in package_files(host, pkg, repo_args) if ko.match(f)}

def loaded_modules(host: Host) -> dict[str, int]:
    """Loaded module name -> reference count."""
    mods: dict[str, int] = {}
    for line in _lines(host.ssh('cat /proc/modules')):
        fields = line.split()
        # The reference count is '-' for modules built without unload support
        mods[fields[0]] = int(fields[2]) if fields[2].isdigit() else -1
    return mods

def is_alt(pkg: str) -> bool:
    return re.search(r'-alt(_|$)', pkg) is not None

@dataclass
class _Group:
    packages: list[str] = field(default_factory=list)
    modules: set[str] = field(default_factory=set)
    conflicts: set[str] = field(default_factory=set)

def group_packages(host: Host, pkgs: list[str], uname: str, repo_args: str) -> list[list[str]]:
    """
    Split not-yet-installed packages into groups that can be installed together: no declared conflict
    between them, and no module name shipped by two packages of the same group (which would be a file
    conflict, or would make it impossible to tell which one modprobe picks).
    """
    groups: list[_Group] = []
    for pkg in pkgs:
        mods = set(package_modules(host, pkg, uname, repo_args))
        if not mods:
            logging.info(f"{pkg} ships no module for {uname}, ignored")
            continue
        conflicts = {line.split()[0] for line in _lines(host.ssh(f'repoquery {repo_args} --conflicts {pkg}'))}
        for g in groups:
            if not (mods & g.modules) and pkg not in g.conflicts and not (conflicts & set(g.packages)):
                break
        else:
            g = _Group()
            groups.append(g)
        g.packages.append(pkg)
        g.modules |= mods
        g.conflicts |= conflicts
    return [g.packages for g in groups]

def unload_modules(host: Host, modules: set[str]) -> set[str]:
    """rmmod the given modules, users first. Returns the ones that could not be unloaded."""
    remaining = set(modules)
    while remaining:
        loaded = loaded_modules(host)
        remaining &= set(loaded)
        unused = sorted(m for m in remaining if loaded[m] == 0)
        if not unused:
            break
        for m in unused:
            host.ssh(f'rmmod {m}', check=False)
    return remaining

def exercise_module(host: Host, pkg: str, name: str, path: str, preloaded: set[str]) -> str | None:
    """modprobe then unload a module and the dependencies it pulled in. Returns an error, or None."""
    if name in preloaded:
        logging.info(f"{name}: already loaded before the test, not touching it")
        return None

    res = host.ssh_with_result(f'modinfo -n {name}')
    if res.returncode != 0:
        return f"{name}: modinfo failed: {res.stdout.strip()}"
    resolved = res.stdout.strip()
    if resolved != path:
        owner = host.ssh_with_result(f"rpm -qf --qf '%{{NAME}}' {resolved}")
        owner_name = owner.stdout.strip() if owner.returncode == 0 else ''
        if owner_name not in ('', 'kernel', pkg):
            # Shadowed by another out-of-tree package installed on the host (e.g. an -alt one).
            logging.warning(f"{name}: modprobe would load {resolved} from {owner_name} instead of {path}, skipped")
            return None
        return f"{name}: modprobe resolves to {resolved} ({owner_name or 'not packaged'}), expected {path}"

    before = set(loaded_modules(host))
    error = None
    res = host.ssh_with_result(f'modprobe -v {name}')
    if res.returncode != 0:
        if 'No such device' in res.stdout:
            # Module code ran, but the hardware it needs (e.g. CPU feature) isn't there.
            logging.warning(f"{name}: modprobe returned ENODEV, ignored: {res.stdout.strip()}")
        elif name in KNOWN_LOAD_FAILURES:
            logging.warning(f"{name}: known load failure ({KNOWN_LOAD_FAILURES[name]}): {res.stdout.strip()}")
        else:
            error = f"{name}: modprobe failed: {res.stdout.strip()}"
    else:
        logging.info(f"{name}: loaded {path}")

    new = set(loaded_modules(host)) - before
    leftover = unload_modules(host, new)
    if leftover:
        error = (error + '; ' if error else f"{name}: ") + f"could not unload {sorted(leftover)}"
    return error

def exercise_installed_packages(host: Host, pkgs: list[str], uname: str, preloaded: set[str]) -> list[str]:
    errors = []
    for pkg in pkgs:
        if host.ssh_with_result(f'rpm -q {pkg}').returncode != 0:
            # e.g. intel-igc obsoletes and provides igc-module: installing the latter installs the former.
            provider = host.ssh_with_result(f"rpm -q --whatprovides {pkg} --qf '%{{NAME}}\\n'")
            if provider.returncode == 0:
                logging.info(f"{pkg} is replaced by {' '.join(_lines(provider.stdout))}, skipped")
            else:
                errors.append(f"[{uname}] {pkg}: not installed")
            continue
        mods = package_modules(host, pkg, uname, None)
        if not mods:
            errors.append(f"[{uname}] {pkg}: installed version ships no module for {uname}")
            continue
        logging.info(f"Testing modules of {pkg}: {sorted(mods)}")
        for name, path in sorted(mods.items()):
            error = exercise_module(host, pkg, name, path, preloaded)
            if error is not None:
                errors.append(f"[{uname}] {pkg}: {error}")
    return errors

def exercise_module_packages(host: Host, uname: str, repo_args: str, enablerepo: str | None) -> list[str]:
    """
    Load and unload the modules of every package built for the running kernel in the given repositories:
    first the installed ones, then the missing default packages and the -alt packages, installed
    temporarily in compatible groups.
    """
    assert host.ssh('uname -r') == uname
    preloaded = set(loaded_modules(host))
    logging.info(f"Modules loaded before testing: {sorted(preloaded)}")

    pkgs = packages_requiring_kernel(host, uname, repo_args)
    assert pkgs, f"No package requiring kernel-uname-r = {uname} found ({repo_args})"
    installed = installed_package_names(host)
    present = [p for p in pkgs if p in installed]
    missing_defaults = [p for p in pkgs if p not in installed and not is_alt(p)]
    missing_alts = [p for p in pkgs if p not in installed and is_alt(p)]
    logging.info(f"Installed module packages: {present}")
    logging.info(f"Default module packages to install: {missing_defaults}")
    logging.info(f"Alternate module packages to install: {missing_alts}")

    errors = exercise_installed_packages(host, present, uname, preloaded)

    groups = group_packages(host, missing_defaults, uname, repo_args) + \
        group_packages(host, missing_alts, uname, repo_args)
    for group in groups:
        before = installed_package_names(host)
        host.yum_install(group, enablerepo=enablerepo)
        try:
            errors += exercise_installed_packages(host, group, uname, preloaded)
        finally:
            # Also removes dependencies pulled in by the group, so that they can't conflict with later groups.
            added = sorted(installed_package_names(host) - before)
            if added:
                host.yum_remove(added)
    return errors

@pytest.mark.reboot
@pytest.mark.usefixtures("host_at_least_8_3")
def test_kernel_preview_update_and_downgrade(kernelpreview_host: Host, kernelpreview_repo: str) -> None:
    host = kernelpreview_host
    errors: list[str] = []

    main_args = ' '.join(f'--repoid={r}' for r in MAIN_REPOS)
    preview_args = f'--repoid={kernelpreview_repo}'
    main_uname = host.ssh('uname -r')
    assert main_uname == repo_kernel_uname(host, main_args), "Host not running the main channel kernel"
    preview_uname = repo_kernel_uname(host, preview_args)
    assert preview_uname != main_uname
    logging.info(f"Main kernel: {main_uname}, preview kernel: {preview_uname}")

    # Update to the preview kernel and modules
    host.yum_update(enablerepos=[kernelpreview_repo])
    host.reboot(verify=True)
    assert host.ssh('uname -r') == preview_uname

    errors += exercise_module_packages(host, preview_uname, preview_args, kernelpreview_repo)

    # Downgrade the kernel, the other packages built from the same SRPM (kernel-devel, kernel-headers, perf...)
    # and everything built against it to the main channel versions
    preview_srpm = host.ssh("rpm -q --qf '%{SOURCERPM}' kernel")
    to_downgrade = [kernel_nvr(host, main_args)] + \
        sorted(set(kernel_srpm_packages(host, preview_srpm) + packages_requiring_kernel(host, preview_uname,
                                                                                        '--installed')))
    logging.info(f"Downgrading: {to_downgrade}")
    host.ssh(f'{host.package_manager} downgrade -y --setopt=protected_packages= {" ".join(to_downgrade)}')
    leftovers = packages_requiring_kernel(host, preview_uname, '--installed')
    assert not leftovers, f"Packages still requiring kernel-uname-r = {preview_uname}: {leftovers}"
    leftovers = kernel_srpm_packages(host, preview_srpm)
    assert not leftovers, f"Packages from {preview_srpm} still installed: {leftovers}"
    host.reboot(verify=True)
    assert host.ssh('uname -r') == main_uname

    errors += exercise_module_packages(host, main_uname, main_args, None)

    assert not errors, "Module load/unload failures:\n" + "\n".join(errors)
