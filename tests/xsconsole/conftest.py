import pytest

def pytest_addoption(parser):
    parser.addoption(
        "--password", 
        action="store", 
        default="", 
        help="Root password of the target node"
    )

    parser.addoption(
        "--nics-reset", 
        action="store", 
        type=int, 
        default="0", 
        help="Reset the NICs"
    )


@pytest.fixture
def host_password(request):
    return request.config.getoption("--password")

@pytest.fixture
def reset_nics(request):
    return request.config.getoption("--nics-reset")
