import logging
import pytest

from lib.installer import AnswerFile

# test the answerfile fixture can run on 2 parametrized instances
# of the test in one run
@pytest.mark.answerfile(lambda: AnswerFile("INSTALL").top_append(
    {"TAG": "source", "type": "local"},
    {"TAG": "primary-disk", "CONTENTS": "nvme0n1"},
))
@pytest.mark.parametrize("parm", [
    1,
    pytest.param(2, marks=[
        pytest.mark.dependency(depends=["TestFixtures::test_parametrized_answerfile[1]"]),
    ]),
])
@pytest.mark.dependency
def test_parametrized_answerfile(answerfile, parm):
    logging.debug("test_parametrized_answerfile with parm=%s", parm)
