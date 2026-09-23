from __future__ import annotations

import pytest

_GITLAB_API = 'https://gitlab.com/api/v4'
# Numeric ID avoids %2F in the URL, which APT decodes and breaks.
_GITLAB_PROJECT_ID = 28547076

_PKG_REGISTRY = f'{_GITLAB_API}/projects/{_GITLAB_PROJECT_ID}/packages/generic'

_RPM_REPO_URL = 'https://xen-project.gitlab.io/xen-guest-agent/rpm-x86_64/'
# DEB uses the numeric project ID so APT doesn't mangle the URL.
_DEB_REPO_URL = f'{_PKG_REGISTRY}/deb-amd64/'


@pytest.fixture(scope="module")
def xen_guest_agent_urls() -> dict[str, str]:
    return {
        'rpm_repo': _RPM_REPO_URL,
        'deb_repo': _DEB_REPO_URL,
    }
