import logging
import os
from typing import Optional, List

import requests

from testbed.schema import (
    TestbedSummary,
    TestbedDetailed,
)
from testbed.sdk.client import TestbedClient

logger = logging.getLogger(__name__)


class TestbedSDK:
    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        base_url = base_url or os.getenv("TESTBED_BASE_URL")
        api_key = api_key or os.getenv("TESTBED_API_KEY")
        assert base_url, "TESTBED_BASE_URL environment variable must be set"
        assert api_key, "TESTBED_API_KEY environment variable must be set"

        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.headers = {"X-API-Key": self.api_key, "Content-Type": "application/json"}

    def _make_request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        url = f"{self.base_url}/{endpoint}"
        response = requests.request(method, url, headers=self.headers, **kwargs)
        response.raise_for_status()
        return response

    def list_testbeds(self) -> List[TestbedSummary]:
        response = self._make_request("GET", "testbeds")
        return [TestbedSummary(**item) for item in response.json()]

    def get_or_create_testbed(
        self, instance_id: str, run_id: str = "default"
    ) -> TestbedSummary:
        data = {"instance_id": instance_id, "run_id": run_id}
        logger.info(f"Creating testbed for instance {instance_id} with run_id {run_id}")
        response = self._make_request("POST", "testbeds", json=data)
        return TestbedSummary(**response.json())

    def create_client(self, instance_id: str, run_id: str = "default") -> TestbedClient:
        testbed = self.get_or_create_testbed(instance_id, run_id)
        return TestbedClient(
            testbed.testbed_id,
            instance_id,
            run_id=run_id,
            base_url=self.base_url,
            api_key=self.api_key,
        )

    def get_testbed(
        self, testbed_id: str, run_id: str = "default"
    ) -> Optional[TestbedDetailed]:
        try:
            response = self._make_request(
                "GET", f"testbeds/{testbed_id}", params={"run_id": run_id}
            )
            return TestbedDetailed(**response.json())
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                return None
            raise

    def delete_testbed(self, testbed_id: str, run_id: str = "default"):
        self._make_request(
            "DELETE", f"testbeds/{testbed_id}", params={"run_id": run_id}
        )

    def delete_all_testbeds(self):
        self._make_request("DELETE", "testbeds")

    def cleanup_user_resources(self):
        self._make_request("POST", "cleanup")
