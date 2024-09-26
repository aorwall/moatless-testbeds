import logging
import os
import base64
import requests
from typing import List, Optional, Dict, Any
from pydantic import BaseModel

from testbed.sdk.client import TestbedClient


class TestbedSummary(BaseModel):
    testbed_id: str
    instance_id: str
    status: str


class TestbedStatusDetailed(BaseModel):
    pod_phase: str
    testbed: Optional[dict] = None
    sidecar: Optional[dict] = None


class TestbedDetailed(BaseModel):
    testbed_id: str
    instance_id: str
    status: Optional[TestbedStatusDetailed] = None
    external_ip: Optional[str] = None


class EvaluationResult(BaseModel):
    run_id: str
    instance_id: str
    patch_applied: bool
    resolved: bool
    tests_status: Dict[str, Any]
    output: Optional[str] = None


class TraceItem(BaseModel):
    file_path: str
    method: Optional[str] = None
    line_number: Optional[int] = None
    output: str = ""


class TestResult(BaseModel):
    status: str
    name: str
    file_path: Optional[str] = None
    method: Optional[str] = None
    failure_output: Optional[str] = None
    stacktrace: List[TraceItem] = []


class TestRunResponse(BaseModel):
    test_results: List[TestResult]
    output: Optional[str] = None

logger = logging.getLogger(__name__)

class TestbedSDK:
    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        base_url = base_url or os.getenv("TESTBED_BASE_URL")
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key or os.getenv("TESTBED_API_KEY")

        assert self.base_url, "TESTBED_BASE_URL environment variable must be set"
        assert self.api_key, "TESTBED_API_KEY environment variable must be set"

        self.headers = {"X-API-Key": api_key}

    def _request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        url = f"{self.base_url}/{endpoint}"
        response = requests.request(method, url, headers=self.headers, **kwargs)
        response.raise_for_status()
        return response.json()

    def list_testbeds(self) -> List[TestbedSummary]:
        data = self._request("GET", "testbeds")
        return [TestbedSummary(**item) for item in data]

    def get_or_create_testbed(self, instance_id: str) -> TestbedDetailed:
        data = self._request("POST", "testbeds", json={"instance_id": instance_id})
        return TestbedDetailed(**data)

    def create_client(self, instance_id: str) -> TestbedClient:
        testbed = self.get_or_create_testbed(instance_id)
        return TestbedClient(testbed.testbed_id, instance_id, base_url=self.base_url, api_key=self.api_key)

    def get_testbed(self, testbed_id: str) -> TestbedStatusDetailed:
        data = self._request("GET", f"testbeds/{testbed_id}")
        return TestbedStatusDetailed(**data)

    def delete_testbed(self, testbed_id: str) -> Dict[str, str]:
        return self._request("DELETE", f"testbeds/{testbed_id}")


    def delete_all_testbeds(self) -> Dict[str, str]:
        return self._request("DELETE", "testbeds")

    def cleanup_user_resources(self) -> Dict[str, int]:
        result = self._request("POST", "cleanup")
        return result
