import base64
import logging
import os
import time
import uuid
from time import sleep
from typing import Dict, Any

import requests
from requests.exceptions import RequestException, HTTPError, Timeout, ConnectionError

from testbed.schema import (
    EvaluationResult,
    CommandExecutionResponse,
    TestRunResponse,
)
from testbed.swebench.constants import ResolvedStatus, APPLY_PATCH_FAIL, RUN_TESTS
from testbed.swebench.log_parsers import parse_log
from testbed.swebench.test_spec import TestSpec
from testbed.swebench.utils import load_swebench_instance

logger = logging.getLogger(__name__)


class TestbedClient:
    def __init__(
        self,
        testbed_id: str,
        instance_id: str,
        run_id: str = "default",
        base_url: str | None = None,
        api_key: str | None = None,
        log_dir: str | None = None,
        ignored_tests: dict[str, list[str]] = {},
    ):
        assert testbed_id, "Testbed ID is required"
        assert instance_id, "SWE-bench instance is required"

        base_url = base_url or os.getenv("TESTBED_BASE_URL")
        api_key = api_key or os.getenv("TESTBED_API_KEY")
        assert base_url, "TESTBED_BASE_URL environment variable must be set"
        assert api_key, "TESTBED_API_KEY environment variable must be set"
        logger.info(f"Initializing Testbed SDK with base URL {base_url}")

        base_url = base_url.rstrip("/")

        self.base_url = base_url
        self.headers = {"X-API-Key": api_key}
        self.api_key = api_key

        self.instance = load_swebench_instance(instance_id)
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

        self.trace_id = uuid.uuid4().hex[:32]
        self.current_span_id = None

    def check_health(self, timeout: int = 30):
        try:
            data = self._request("GET", "health")
            return data.get("status") == "OK"
        except requests.RequestException as e:
            logger.error(f"Error during ping: {str(e)}")
            return False

    def _generate_traceparent(self):
        return f"00-{self.trace_id}-{self.current_span_id or uuid.uuid4().hex[:16]}-01"

    def _request(
        self,
        method: str,
        endpoint: str | None = None,
        max_retries: int = 3,
        initial_retry_delay: int = 1,
        max_retry_delay: int = 60,
        operation_timeout: int = 300,
        **kwargs,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}/testbeds/{self.testbed_id}"
        if endpoint:
            url += f"/{endpoint}"

        headers = {
            "X-API-Key": self.api_key,
            "traceparent": self._generate_traceparent(),
        }

        retries = 0
        retry_delay = initial_retry_delay
        start_time = time.time()

        while retries < max_retries:
            if time.time() - start_time > operation_timeout:
                raise TimeoutError(
                    f"Operation timed out after {operation_timeout} seconds"
                )

            try:
                logger.debug(
                    f"Attempting request to {url} (Attempt {retries + 1}/{max_retries})"
                )
                response = requests.request(
                    method, url, headers=headers, timeout=30, **kwargs
                )
                response.raise_for_status()
                logger.debug(f"Request to {url} successful")
                return response.json()
            except (RequestException, HTTPError) as e:
                retries += 1
                if isinstance(e, HTTPError) and e.response.status_code < 500:
                    logger.error(
                        f"Client error during request to {url}: {e}. Response: {e.response.text}"
                    )
                    raise
                if retries == max_retries:
                    logger.error(f"Max retries reached for {url}: {e}")
                    raise

                if isinstance(e, Timeout):
                    logger.warning(f"Request to {url} timed out. Retrying...")
                elif isinstance(e, ConnectionError):
                    logger.warning(f"Connection error occurred for {url}. Retrying...")
                else:
                    logger.warning(f"Error during request to {url}: {e}. Retrying...")

                logger.info(
                    f"Retrying in {retry_delay} seconds... (Attempt {retries + 1}/{max_retries})"
                )
                time.sleep(retry_delay)
                retry_delay = min(
                    retry_delay * 2, max_retry_delay
                )  # Exponential backoff with max delay

        raise Exception(f"Unexpected error: Max retries reached for {url}")

    def wait_until_ready(self, timeout: float = 600):
        start_time = time.time()
        while time.time() - start_time < timeout:
            response = self._request("GET", "status")
            if response.get("status") == "NotFound":
                raise Exception(f"Testbed {self.testbed_id} not found")
            if response.get("status") == "Running":
                return True
            time.sleep(1)
        raise TimeoutError(
            f"Testbed {self.testbed_id} not ready within {timeout} seconds"
        )

    def status(self):
        return self._request("GET", "status")

    def get_execution_status(self) -> CommandExecutionResponse:
        try:
            response = self._request("GET", "exec")
            return CommandExecutionResponse.model_validate(response)
        except requests.RequestException as e:
            logger.error(f"Error during get_execution_status: {str(e)}")
            raise e

    def get_diff(self) -> str:
        """Get the current git diff output."""
        try:
            response = self._request("GET", "diff")
            return response.get("diff", "")
        except requests.RequestException as e:
            logger.error(f"Error getting git diff: {str(e)}")
            raise e

    def apply_patch(self, patch: str) -> str:
        if not patch.endswith("\n"):
            patch += "\n"

        response = self._request("POST", "apply-patch", json={"patch": patch})

        if APPLY_PATCH_FAIL in response.get("output", ""):
            logger.error(
                f"Failed to apply patch: {patch}.\n\nOutput\n:{response.get('output', '')}"
            )
            raise RuntimeError(
                f"Failed to apply patch: {patch}.\n\nOutput\n:{response.get('output', '')}"
            )

        diff = self.get_diff()
        logger.debug(f"Diff after patch: \n{diff}")
        return diff

    def run_tests(
        self, test_files: list[str] | None = None, patch: str | None = None
    ) -> TestRunResponse:
        logger.debug(f"Executing run_tests with test_files={test_files} and patch={patch}")
        if patch:
            self.apply_patch(patch)

        data = {"test_files": test_files} if test_files else {}
        self._request("POST", "run-tests", json=data)
        response = self.get_execution_status()

        start_time = time.time()
        while response.status == "running":
            if time.time() - start_time > 1200:
                raise TimeoutError("Execution timed out after 1200 seconds")
            sleep(0.1)
            response = self.get_execution_status()

        if self.log_dir:
            datetime_str = time.strftime("%Y%m%d-%H%M%S")
            with open(f"{self.log_dir}/{datetime_str}_run_tests.log", "a") as f:
                f.write(f"Response:\n{response.output}\n")

        log = response.output.split(f"{RUN_TESTS}\n")[-1]
        test_result = parse_log(log, self.test_spec.repo)

        filtered_test_result = []

        statuses = {}

        ignored_tests = 0
        for test in test_result:
            if test.method in self.ignored_tests.get(test.file_path, []):
                ignored_tests += 1
                continue

            filtered_test_result.append(test)

            if test.status not in statuses:
                statuses[test.status] = 0

            statuses[test.status] += 1

        if ignored_tests:
            logger.info(
                f"Did run {len(test_result)} tests, ignored {ignored_tests} tests. {statuses}"
            )
        else:
            logger.info(f"Did run {len(test_result)} tests. {statuses}")

        return TestRunResponse(test_results=filtered_test_result, output=response.output)

    def run_evaluation(
        self, run_id: str | None = None, patch: str | None = None
    ) -> EvaluationResult:
        self.current_span_id = uuid.uuid4().hex[:16]
        if not self.instance:
            raise ValueError("SWE-bench instance not set")

        try:
            if not patch:
                logger.info(
                    f"Running evaluation for instance {self.instance.instance_id} with gold prediction"
                )
                patch = self.instance.patch
            else:
                logger.info(
                    f"Running evaluation for instance {self.instance.instance_id} with patch"
                )

            self.wait_until_ready()

            self.apply_patch(patch)

            try:
                git_diff_output_before = self.get_diff().strip()
            except Exception as e:
                logger.warning(
                    f"Failed to get git diff before running eval script: {e}"
                )
                git_diff_output_before = None

            self._request("POST", "run-evaluation")

            response = self.get_execution_status()
            while response.status == "running":
                response = self.get_execution_status()
                sleep(1)

            if "APPLY_PATCH_FAIL" in response.output:
                logger.error("Failed to apply patch")
                return EvaluationResult(
                    status="error",
                    output=response.get("output", ""),
                )

            try:
                git_diff_output_after = self.get_diff().strip()

                if (
                    git_diff_output_before
                    and git_diff_output_after != git_diff_output_before
                ):
                    logger.info(f"Git diff changed after running eval script")
            except Exception as e:
                logger.warning(f"Failed to get git diff after running eval script: {e}")

            test_status = self.test_spec.get_pred_report(response.output)
            return EvaluationResult(
                run_id=run_id,
                resolved=test_status.status == ResolvedStatus.FULL,
                patch_applied=True,
                instance_id=self.instance.instance_id,
                output=response.output,
                tests_status=test_status,
            )
        finally:
            self.current_span_id = None

    def reset_testbed(self):
        try:
            response = self._request(
                "POST",
                "reset",
                json={"instance_id": self.instance.instance_id, "run_id": self.run_id},
            )
            logger.info(f"Reset testbed {self.testbed_id}: {response}")
            if not response.get("success", False):
                raise Exception("Failed to reset testbed")
            return response
        except requests.RequestException as e:
            logger.error(f"Error during reset: {str(e)}")
            raise e

    def reset(self):
        self.run_tests()
        diff = self.get_diff()
        logger.debug(f"Diff after patch: \n{diff}")
        return diff

    def save_file(self, file_path: str, content: str):
        try:
            encoded_content = base64.b64encode(content.encode()).decode()
            data = {"file_path": file_path, "content": encoded_content}
            logger.debug(f"Saving file: {file_path}")
            response = self._request("POST", "file", json=data)
            return response
        except requests.RequestException as e:
            logger.error(f"Error saving file {file_path}: {str(e)}")
            raise e
        finally:
            if self.log_dir:
                datetime_str = time.strftime("%Y%m%d-%H%M%S")
                with open(f"{self.log_dir}/{datetime_str}_save_file.log", "a") as f:
                    f.write(f"File path: {file_path}\n")
                    f.write(f"Content:\n{content}\n")

    def get_file(self, file_path: str):
        try:
            params = {"file_path": file_path}
            response = self._request("GET", "file", params=params)
            if "content" in response:
                return base64.b64decode(response["content"]).decode()
            else:
                return response
        except requests.RequestException as e:
            logger.error(f"Error getting file: {str(e)}")
            return {"error": str(e)}

    def destroy(self):
        self._request("DELETE")
