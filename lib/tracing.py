from __future__ import annotations

from typing import Any

import requests
import time
from urllib.parse import urlparse

# for debugging
import logging

class Tracing:
    def __init__(self, endpoint, enabled):
        self.endpoint: str | None = endpoint
        self.enabled: bool = enabled
        # an observer uuid could be passed by the tracing fixture if needed by some tests, but not needed for now

    def locate_span(self, operation, tag, value) -> Any:
        # url = urlparse(self.endpoint)
        # api = f"{url.scheme}://{url.netloc}/api/v2/traces"
        if not self.enabled or not self.endpoint:
            return None

        # `export-interval` setting in xapi.conf dictates how often tracing will export spans to endpoints
        retries = 15
        for _ in range(retries):
            logging.debug(f'sending HTTP request for spanName {operation}')
            response = requests.get(self.endpoint, params={"spanName": operation})
            # FIXME error here when calling json decoding, even with url parsing
            data = response.json()
            if not data:
                logging.debug("no data from HTTP request")
                return None
            logging.debug(
                f'received span data, looking for span with name {operation} and tag {tag} with value {value}')
            for spans in data:
                for span in spans:
                    if span.get('name') == operation:
                        if span.get('tags', {}).get(tag) == value:
                            return span
            time.sleep(5)
        return None
