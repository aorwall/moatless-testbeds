import logging
import os
import uuid
from typing import Dict, Any, List
import hashlib

import requests

from testbeds.schema import (
    EvaluationResult,
    CommandExecutionResponse,
    TestRunResponse, 
    SWEbenchInstance,
    TestStatus,
)
from testbeds.swebench.constants import ResolvedStatus, APPLY_PATCH_FAIL, RUN_TESTS
from testbeds.swebench.log_parsers import parse_log
from testbeds.swebench.test_spec import TestSpec
from testbeds.swebench.utils import load_swebench_instance
from testbeds.sdk.exceptions import (
    TestbedAuthenticationError,
    TestbedError,
    TestbedConnectionError,
    TestbedTimeoutError,
)

logger = logging.getLogger(__name__)

class BaseTestbedClient:
    def __init__(
        self,
        testbed_id: str | None = None,
        instance_id: str | None = None,
        instance: SWEbenchInstance | None = None,
        dataset_name: str | None = None,
        run_id: str = "default",
        base_url: str | None = None,
        api_key: str | None = None,
        log_dir: str | None = None,
        ignored_tests: dict[str, list[str]] = {},
        test_cache: dict | None = None,
    ):

        if not instance_id and not instance:
            raise ValueError("Either instance_id or instance must be set")

        base_url = base_url or os.getenv("TESTBED_BASE_URL")
        api_key = api_key or os.getenv("TESTBED_API_KEY")
        assert base_url, "TESTBED_BASE_URL environment variable must be set"
        assert api_key, "TESTBED_API_KEY environment variable must be set"

        base_url = base_url.rstrip("/")

        self.base_url = base_url
        self.headers = {"X-API-Key": api_key}
        self.api_key = api_key

        self.trace_id = uuid.uuid4().hex[:32]
        self.current_span_id = None

        if not instance:
            self.instance = self._load_instance(instance_id)
        else:
            self.instance = instance

        logger.info(f"Create Testbed Client with base URL {base_url} for instance {self.instance.instance_id} and testbed {testbed_id}")

        self.test_spec = TestSpec.from_instance(self.instance)

        self.testbed_id = testbed_id
        self.run_id = run_id
        self.ignored_tests = ignored_tests

        if log_dir:
            self.log_dir = f"{log_dir}/{testbed_id}" if log_dir else None
            if not os.path.exists(self.log_dir):
                os.makedirs(self.log_dir)
        else:
            self.log_dir = None

        self.test_cache = test_cache
        self._current_patch = None

    def _generate_traceparent(self):
        return f"00-{self.trace_id}-{self.current_span_id or uuid.uuid4().hex[:16]}-01"

    def _generate_headers(self):
        return {
            "X-API-Key": self.api_key,
            "traceparent": self._generate_traceparent(),
        }

    def _generate_cache_key(self, test_files: list[str] | None, patch: str | None) -> str:
        """Generate a unique cache key based on test files and patch content"""
        key_parts = []
        if test_files:
            key_parts.extend(sorted(test_files))
        if patch:
            key_parts.append(patch)
        if not key_parts:
            key_parts.append("all_tests_no_patch")
            
        combined = "|".join(key_parts)
        return hashlib.sha256(combined.encode()).hexdigest()

    def _get_url(self, endpoint: str | None = None) -> str:
        url = f"{self.base_url}/testbeds/{self.testbed_id}"
        if endpoint:
            url += f"/{endpoint}"
        return url 

    def _load_instance(self, instance_id: str) -> SWEbenchInstance:
        """Load a SWEbench instance from the API."""
        try:
            url = f"{self.base_url}/instances/{instance_id}"
            response = requests.get(url, headers=self._generate_headers())
            response.raise_for_status()
            return SWEbenchInstance.model_validate(response.json())
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to load instance {instance_id} from API: {e}")
            raise TestbedError(f"Failed to load instance {instance_id}") from e
