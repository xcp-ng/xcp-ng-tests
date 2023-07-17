# Explicitly import package-scoped fixtures (see explanation in pkgfixtures.py)
from pkgfixtures import host_with_saved_yum_state

# Requirements:
# From --hosts parameter:
# - host(A1): any master host of a pool, with access to XCP-ng RPM repositories and reports.xcp-ng.org.

def test_extra_group_packages_url_resolved(host, extra_pkgs):
    for p in extra_pkgs:
        host.ssh(['yumdownloader', '--resolve', '--urls', p])

def test_extra_group_packages_can_be_installed(host_with_saved_yum_state, extra_pkgs):
    # Just try to install all packages together. Installing them one by one
    # takes too much time due to the generation of the initrd.
    host_with_saved_yum_state.yum_install(extra_pkgs)
