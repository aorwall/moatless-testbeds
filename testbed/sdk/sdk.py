import os

import requests
import logging
import time
from typing import Optional, List, Dict, Any

from testbed.schema import (
    TestbedSummary,
    TestbedDetailed,
    TestRunResponse,
    EvaluationResult,
    CommandExecutionResponse,
)
from testbed.sdk.client import TestbedClient

logger = logging.getLogger(__name__)

class TestbedSDK:
    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        base_url = base_url or os.getenv("TESTBED_BASE_URL")
        api_key = api_key or os.getenv("TESTBED_API_KEY")
        assert base_url, "TESTBED_BASE_URL environment variable must be set"
        assert api_key, "TESTBED_API_KEY environment variable must be set"

        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.headers = {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json"
        }

    def _make_request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        url = f"{self.base_url}/{endpoint}"
        response = requests.request(method, url, headers=self.headers, **kwargs)
        response.raise_for_status()
        return response

    def list_testbeds(self) -> List[TestbedSummary]:
        response = self._make_request("GET", "testbeds")
        return [TestbedSummary(**item) for item in response.json()]

    def get_or_create_testbed(self, instance_id: str) -> TestbedSummary:
        data = {"instance_id": instance_id}
        response = self._make_request("POST", "testbeds", json=data)
        return TestbedSummary(**response.json())

    def create_client(self, instance_id: str) -> TestbedClient:
        testbed = self.get_or_create_testbed(instance_id)
        return TestbedClient(testbed.testbed_id, instance_id, base_url=self.base_url, api_key=self.api_key)

    def get_testbed(self, testbed_id: str) -> Optional[TestbedDetailed]:
        try:
            response = self._make_request("GET", f"testbeds/{testbed_id}")
            return TestbedDetailed(**response.json())
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                return None
            raise

    def delete_testbed(self, testbed_id: str):
        self._make_request("DELETE", f"testbeds/{testbed_id}")

    def execute_command(self, testbed_id: str, commands: List[str], timeout: int = 60) -> CommandExecutionResponse:
        data = {"commands": commands, "timeout": timeout}
        response = self._make_request("POST", f"testbeds/{testbed_id}/exec", json=data)
        return CommandExecutionResponse(**response.json())

    def get_file(self, testbed_id: str, file_path: str) -> str:
        response = self._make_request("GET", f"testbeds/{testbed_id}/file", params={"file_path": file_path})
        import base64
        return base64.b64decode(response["content"]).decode()

    def save_file(self, testbed_id: str, file_path: str, content: str):
        import base64
        encoded_content = base64.b64encode(content.encode()).decode()
        data = {"file_path": file_path, "content": encoded_content}
        self._make_request("POST", f"testbeds/{testbed_id}/file", json=data)

    def delete_all_testbeds(self):
        self._make_request("DELETE", "testbeds")

    def cleanup_user_resources(self):
        self._make_request("POST", "cleanup")

    def run_tests(self, testbed_id: str, test_files: List[str] | None = None, patch: str | None = None) -> TestRunResponse:
        data = {}
        if test_files:
            data["test_files"] = test_files
        if patch:
            data["patch"] = patch
        response = self._make_request("POST", f"testbeds/{testbed_id}/run-tests", json=data)
        return TestRunResponse(**response.json())

    def run_evaluation(self, testbed_id: str, run_id: str | None = None, patch: str | None = None) -> EvaluationResult:
        data = {}
        if run_id:
            data["run_id"] = run_id
        if patch:
            data["patch"] = patch
        result = self._make_request("POST", f"testbeds/{testbed_id}/run-evaluation", json=data)
        return EvaluationResult(**result)
