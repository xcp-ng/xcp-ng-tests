from __future__ import annotations

from typing import Any

import requests
import time
from urllib.parse import urlparse

class Tracing:
    def __init__(self, enabled, endpoint):
        self.enabled: bool = enabled
        self.endpoint: str | None = endpoint # not None if enabled is true
        # an observer uuid could be passed by the tracing fixture if needed by some tests, but no need for now

    def locate_span(self, operation, tag) -> Any:
        url = urlparse(self.endpoint)
        api = f"{url.scheme}://{url.netloc}/api/v2/traces"
        retries = 15
        for _ in range(retries):
            response = requests.get(api, params={"spanName": operation})
            data = response.json()
            if not data:
                return None

            for spans in data:
                for span in spans:
                    if span.get('name') == operation:
                        if span.get('tags', {}).get('test.tag') == tag:
                            return span
            time.sleep(5)
        return None
