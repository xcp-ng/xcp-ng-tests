import logging
import pytest
import time
import uuid
from urllib.parse import urlparse
import requests

@pytest.fixture
def tracing_endpoint(request):
    endpoint = request.config.getoption("--tracing-endpoint")
    if endpoint is None:
        pytest.exit("Missing required option: --tracing-endpoint")
    return endpoint

def locate_span(data, operation, tag):
    if not data:
        return None

    for spans in data:
        for span in spans:
            if span.get('name') == operation:
                if span.get('tags', {}).get('test.tag') == tag:
                    return span

def test_tracing(tracing_endpoint, host):
    operation = "xe observer-list"
    url = urlparse(tracing_endpoint)
    api = f"{url.scheme}://{url.netloc}/api/v2/traces"
    tag = str(uuid.uuid4())
    logging.info(f"Peforming operation tagged with: {tag}")
    o = host.ssh(f'BAGGAGE="test.tag={tag}" {operation}')
    params = { "spanName": operation }

    logging.info(f"Querying endpoint to locate our root span")
    root_span = None
    for i in range(15):
        response = requests.get(api, params=params)
        data = response.json()
        root_span = locate_span(data, operation, tag)
        if root_span:
            break
        time.sleep(5)

    if not root_span:
        pytest.fail("Could not find our operation's span in time")
