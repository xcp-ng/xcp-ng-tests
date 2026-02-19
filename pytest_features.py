# pytest_features.py

import pytest
from collections import defaultdict
import pickle

def pytest_addoption(parser):
    group = parser.getgroup("features")
    group.addoption(
        "--list-features",
        action="store_true",
        help="List features tested by the test suite and exit",
    )


def pytest_configure(config):
    config._features_index = defaultdict(set)
    config.option.collectonly = True


def pytest_collection_modifyitems(config, items):
    """
    Collect feature metadata from tests.
    """
    for item in items:
        test_func = getattr(item, "function", None)
        if test_func and hasattr(test_func, "_features"):
            for feature in test_func._features:
                config._features_index[feature].add(item.nodeid)


def pytest_sessionfinish(session, exitstatus):
    config = session.config
    if not config.getoption("--list-features"):
        return

    features_index = config._features_index

    if not features_index:
        print("No features declared.")
    else:
        print("\nFeatures tested:\n")
        for feature in sorted(features_index):
            print(f"- {feature}")
            for test in sorted(features_index[feature]):
                print(f"    {test}")

    # Exit without running tests
    pytest.exit("Features listed", returncode=0)

