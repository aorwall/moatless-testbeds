import logging
import os
from typing import Optional, List

import requests

from testbeds.schema import (
    TestbedSummary,
    TestbedDetailed, SWEbenchInstance,
)
from testbeds.sdk.client import TestbedClient
from testbeds.sdk.exceptions import (
    TestbedError,
    TestbedConnectionError,
    TestbedAuthenticationError,
    TestbedTimeoutError,
    TestbedValidationError,
)

logger = logging.getLogger(__name__)


class TestbedSDK:
    def __init__(self, base_url: str | None = None, api_key: str | None = None, enable_cache: bool = False):
        base_url = base_url or os.getenv("TESTBED_BASE_URL")
        api_key = api_key or os.getenv("TESTBED_API_KEY")
        assert base_url, "TESTBED_BASE_URL environment variable must be set"
        assert api_key, "TESTBED_API_KEY environment variable must be set"

        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.headers = {"X-API-Key": self.api_key, "Content-Type": "application/json"}
        self.enable_cache = enable_cache
        self._test_cache = {} if enable_cache else None

    def _make_request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        url = f"{self.base_url}/{endpoint}"
        try:
            response = requests.request(method, url, headers=self.headers, **kwargs)
            response.raise_for_status()
            return response
            
        except requests.exceptions.ConnectionError as e:
            raise TestbedConnectionError(f"Failed to connect to testbed: {str(e)}") from e
        except requests.exceptions.Timeout as e:
            raise TestbedTimeoutError(f"Request timed out: {str(e)}") from e
        except requests.exceptions.HTTPError as e:
            error_code = None
            details = {}

            try:
                error_data = e.response.json()
                error_message = error_data.get('message')
                error_code = error_data.get('error')
                details = error_data
            except (ValueError, AttributeError):
                error_message = e.response.text or str(e)

            if 400 <= e.response.status_code < 500:
                if e.response.status_code == 401:
                    raise TestbedAuthenticationError(error_message, error_code, details) from e
            raise TestbedError(error_message, error_code, details) from e
                
        except Exception as e:
            raise TestbedError(f"Unexpected error: {str(e)}") from e

    def list_testbeds(self) -> List[TestbedSummary]:
        response = self._make_request("GET", "testbeds")
        return [TestbedSummary(**item) for item in response.json()]

    def get_or_create_testbed(
        self, instance_id: str, run_id: str = "default"
    ) -> TestbedSummary:
        if not instance_id:
            raise TestbedValidationError("instance_id is required")

        data = {"instance_id": instance_id, "run_id": run_id}
        logger.info(f"Creating testbed for instance {instance_id} with run_id {run_id}")
        response = self._make_request("POST", "testbeds", json=data)
        return TestbedSummary(**response.json())

    def create_client(self,
                      instance_id: str | None = None,
                      instance: dict | SWEbenchInstance | None = None,
                      dataset_name: str = "princeton-nlp/SWE-bench_Lite",
                      run_id: str = "default") -> TestbedClient:
        if not instance_id and not instance:
            raise ValueError("Either instance_id or instance must be provided")

        if instance and isinstance(instance, dict):
            instance = SWEbenchInstance.model_validate(instance)

        instance_id = instance_id or instance.instance_id
        testbed = self.get_or_create_testbed(instance_id, run_id)
        return TestbedClient(
            testbed.testbed_id,
            instance_id=instance_id,
            instance=instance,
            dataset_name=dataset_name,
            run_id=run_id,
            base_url=self.base_url,
            api_key=self.api_key,
            test_cache=self._test_cache if self.enable_cache else None,
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

    def clear_cache(self):
        """Clear the test results cache"""
        if self._test_cache is not None:
            self._test_cache.clear()

    def __del__(self):
        """Cleanup when SDK is deleted"""
        self.clear_cache()
