import pytest

import logging
import uuid

from lib.host import Host
from lib.tracing import Tracing

# Requirements:
# From --hosts parameter:
# - a XCP-ng host
# Optionally a --tracing-endpoint parameter with a reachable endpoint

@pytest.mark.small_vm
class TestTracing:
    # A very simple test with optional tracing support
    def test_tracing(self, host: Host, tracing: Tracing):
        # do what needs to be tested but provide a trace identifier for the operations we want to trace
        operation = "xe observer-list"
        tag = 'test.tag'
        tag_value = str(uuid.uuid4())
        logging.info(f"Peforming {operation} tagged with: {tag}")
        ret = host.xe(operation, vars={'BAGGAGE': {tag: tag_value}})
        # other form for env variables with only one value: host.xe(operation, vars={'TRACEPARENT': str(uuid.uuid4())})

        # validation of the test
        assert ret == 0

        # output additional tracing stats
        if tracing.enabled:
            span = tracing.locate_span(operation, tag, tag_value)
            if span:
                logging.info(f'{operation} duration: {span.get("duration")}')
            else:
                logging.warning("Could not find our operation's span")
