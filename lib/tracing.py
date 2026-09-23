from __future__ import annotations

import string
import time
from random import choice
from urllib.parse import urlparse

import requests

from typing import Any

# NOTE for debugging
# import logging

class Tracing:
    def __init__(self, endpoint, enabled) -> None:
        self.endpoint: str | None = endpoint
        self.enabled: bool = enabled
        # NOTE an observer uuid could be passed by the tracing fixture if needed by some tests, but not needed for now

    def gen_traceparent(self) -> str:
        # W3C: https://www.w3.org/TR/trace-context/#traceparent-header
        version = "00"
        trace_id = ''.join(choice(string.hexdigits) for _ in range(32)).lower()
        parent_id = ''.join(choice(string.hexdigits) for _ in range(16)).lower()
        trace_flags = "01"
        return '-'.join([version, trace_id, parent_id, trace_flags])

    def locate_span(self, name: str, tag: str, value: str) -> Any:
        # NOTE in the future maybe parsing the endpoint URL could allow to recognize the type of endpoint to then call sub-functions
        url = urlparse(self.endpoint)
        api = f"{url.scheme}://{url.netloc}/api/v2/traces"
        if not self.enabled or not self.endpoint:
            return None

        retries = 12
        for _ in range(retries):
            # logging.debug(f'sending Zipkin HTTP request for spanName {operation}')
            # http://10.1.38.10:9411/api/v2/traces?spanName=xe+observer-list&tagQuery=test.uuid worked for latest Zipkin docker image
            # http://10.1.38.10:16686/search?operation=xe%20observer-list&service=xapi&tags={%22xs.observer.uuid%22%3A%22b2fc74d7-9f08-3ef8-16b5-09a0e748dd7a%22} needed for latest Jaeger docker image, response looks parsable, but using zipkin port 9411 always returns "unexpected end of JSON input"
            response = requests.get(api, params={"spanName": name, "tagQuery": tag})
            # logging.debug(f'request URL: {response.url}')
            data = response.json()
            # logging.debug(
            # f'received traces data, looking for span with name {operation} and tag {tag} with value {value}')
            for trace in data:
                for span in trace:
                    if span.get('name') == name and span.get('tags', {}).get(tag) == value:
                        return span
            time.sleep(5)
        return None
