from __future__ import annotations

import pytest

import logging

from lib.host import Host

from typing import Generator

@pytest.fixture(scope='module')
def kernelpreview_repo(request: pytest.FixtureRequest) -> str:
    """Repository id of the preview kernel channel (--kernelpreview-repo)."""
    return str(request.config.getoption('--kernelpreview-repo'))

def _restore_packages(host: Host, saved_packages: list[str], saved_tid: int, preview_repo: str) -> None:
    if host.packages() == saved_packages:
        return
    logging.info(f"[{host}] Rolling back yum transactions after #{saved_tid}")
    # The kernel is a protected package, rolling back a kernel change needs protected_packages emptied.
    # The saved state may include packages from the preview repository, if they were installed before the test.
    host.ssh(f'{host.package_manager} history rollback -y --setopt=protected_packages='
             f' --enablerepo=xcp-ng-base,xcp-ng-updates,{preview_repo} {saved_tid}')
    pkgs = host.packages()
    missing = [p for p in saved_packages if p not in set(pkgs)]
    extra = [p for p in pkgs if p not in set(saved_packages) and not p.startswith('gpg-pubkey-')]
    assert not missing and not extra, \
        f"Yum state badly restored. Missing: [{' '.join(missing)}], extra: [{' '.join(extra)}]"

@pytest.fixture(scope='module')
def kernelpreview_host(host: Host, kernelpreview_repo: str, request: pytest.FixtureRequest) \
        -> Generator[Host, None, None]:
    """
    Host up to date with the main repositories, with the (disabled) preview repository defined.

    On teardown, the yum state is rolled back to what it was after the main update, and the host is
    rebooted if it isn't running the kernel it was running then.
    """
    repo = kernelpreview_repo
    repo_created = False
    if host.ssh_with_result(f"grep -qs '^\\[{repo}\\]' /etc/yum.repos.d/*.repo").returncode != 0:
        assert repo.startswith('xcp-ng-'), f"{repo} is not defined on {host} and can't be added automatically"
        base_repo = str(request.config.getoption('--kernelpreview-base-repo'))
        logging.info(f"[{host}] Adding the {repo} repository from {base_repo} (disabled)")
        host.add_xcpng_repo(repo.removeprefix('xcp-ng-'), base_repo, enabled=False)
        repo_created = True

    try:
        # Step 1: regular update from the main channel, rebooting only if something was updated.
        host.update()
        base_kernel = host.ssh('uname -r')
        saved_packages = host.packages()
        saved_tid = host.get_last_yum_history_tid()
        logging.info(f"[{host}] Base kernel after the main update: {base_kernel}")

        yield host

        _restore_packages(host, saved_packages, saved_tid, repo)
        if host.ssh('uname -r') != base_kernel:
            host.reboot(verify=True)
        assert host.ssh('uname -r') == base_kernel
    finally:
        if repo_created:
            host.remove_xcpng_repo(repo.removeprefix('xcp-ng-'))
