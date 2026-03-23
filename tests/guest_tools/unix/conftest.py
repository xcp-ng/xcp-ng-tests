import pytest

import os
import tempfile
import zipfile

import requests

from lib.common import url_download

_GITLAB_API = 'https://gitlab.com/api/v4/projects/xen-project%2Fxen-guest-' \
              'agent/jobs/artifacts/main/download?search_recent_successful_' \
              'pipelines=true&job='


def _extract_from_zip(zip_path, suffix, dest_dir):
    """
    Extract the main package matching suffix from zip_path into dest_dir.
    Excludes debug/dbgsym packages which may also be present.
    """
    with zipfile.ZipFile(zip_path) as zf:
        matches = [
            n for n in zf.namelist()
            if n.endswith(suffix) and not any(
                kw in os.path.basename(n) for kw in ('debug', 'dbgsym')
            )
        ]
        assert len(matches) == 1, \
            f"Expected exactly one non-debug *{suffix} in artifact zip, found: {matches}"
        zf.extract(matches[0], dest_dir)
        return os.path.join(dest_dir, matches[0])


@pytest.fixture(scope="module")
def xen_guest_agent_packages():
    """
    Download the latest xen-guest-agent RPM and DEB from GitLab CI artifacts.
    Yields a dict with keys 'rpm' and 'deb' pointing to the local file paths.
    """
    artifact_urls = {'rpm': f'{_GITLAB_API}pkg-rpm-x86_64',
                     'deb': f'{_GITLAB_API}pkg-deb-amd64'}

    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = os.path.join(tmpdir, 'artifacts.zip')

        url_download(artifact_urls['rpm'], zip_path)
        rpm_path = _extract_from_zip(zip_path, '.rpm', tmpdir)

        url_download(artifact_urls['deb'], zip_path)
        deb_path = _extract_from_zip(zip_path, '.deb', tmpdir)

        yield {'rpm': rpm_path, 'deb': deb_path}
