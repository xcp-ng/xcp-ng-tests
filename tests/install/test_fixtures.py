import logging
import pytest

class TestFixtures:
    # test the answerfile fixture can run on 2 parametrized instances
    # of the test in one run
    @pytest.mark.answerfile(
        {
            "base": "INSTALL",
            "source": {"type": "local"},
            "primary-disk": {"text": "nvme0n1"},
        })
    @pytest.mark.parametrize("parm", [
        1,
        pytest.param(2, marks=[
            pytest.mark.dependency(depends=["TestFixtures::test_parametrized_answerfile[1]"]),
        ]),
    ])
    @pytest.mark.dependency
    def test_parametrized_answerfile(self, answerfile, parm):
        logging.debug("test_parametrized_answerfile with parm=%s", parm)
