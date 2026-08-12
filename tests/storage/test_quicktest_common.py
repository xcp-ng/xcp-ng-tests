from __future__ import annotations

import pytest

from lib.sr import SR

@pytest.mark.quicktest
def test_common_quicktest(local_sr_on_hostA1: SR) -> None:
    local_sr_on_hostA1.run_quicktest(sr_specific=False)
