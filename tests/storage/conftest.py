def pytest_collection_modifyitems(config, items):
    # modify ordering so that ext is always tested first,
    # before more complex storage drivers
    for item in reversed(list(items)):
        if "_ext_" in item.path.name:
            items.remove(item)
            items.insert(0, item)
