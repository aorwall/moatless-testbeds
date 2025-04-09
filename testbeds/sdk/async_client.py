import asyncio
import logging
import time
from typing import Any, Dict, List
import uuid
import signal
import atexit
import weakref
import threading
from datetime import datetime

import aiohttp
from aiohttp import ClientTimeout
from aiohttp.client_exceptions import (
    ClientConnectionError,
    ClientResponseError,
    ContentTypeError,
)

from testbeds.schema import (
    CommandExecutionResponse,
    SWEbenchInstance,
    TestRunResponse,
    TestStatus,
    EvaluationResult,
    TestbedSummary,
)
from testbeds.swebench.constants import APPLY_PATCH_FAIL, RUN_TESTS, ResolvedStatus
from testbeds.sdk.base_client import BaseTestbedClient
from testbeds.sdk.exceptions import (
    TestbedAuthenticationError,
    TestbedError,
    TestbedConnectionError,
    TestbedTimeoutError,
    TestbedValidationError,
)
from testbeds.swebench.log_parsers import parse_log

from opentelemetry import trace

tracer = trace.get_tracer(__name__)
logger = logging.getLogger(__name__)


class AsyncTestbedClient(BaseTestbedClient):
    _cleanup_registered = False
    _cleanup_lock = asyncio.Lock()  # Class level lock for cleanup registration

    def __init__(
        self,
        testbed_id: str | None = None,
        instance: SWEbenchInstance | None = None,
        dataset_name: str | None = None,
        run_id: str = "default",
        base_url: str | None = None,
        api_key: str | None = None,
        log_dir: str | None = None,
        ignored_tests: dict[str, list[str]] = {},
        test_cache: dict | None = None,
        host: str = "localhost",
        port: int = 8000,
        timeout: float = 60.0,
        ssl: bool = False,
        auth_token: str | None = None,
        verify_ssl: bool = True,
        persist_testbed: bool = False,
    ):
        super().__init__(
            testbed_id=testbed_id,
            instance=instance,
            dataset_name=dataset_name,
            run_id=run_id,
            base_url=base_url,
            api_key=api_key,
            log_dir=log_dir,
            ignored_tests=ignored_tests,
            test_cache=test_cache,
        )
        self.session: aiohttp.ClientSession | None = None
        self.persist_testbed = persist_testbed

    async def __aenter__(self):
        """Enter the async context manager."""
        with tracer.start_as_current_span("testbed_setup") as span:
            try:
                if not self.testbed_id:
                    testbed = await self.get_or_create_testbed_async()
                    self.testbed_id = testbed.testbed_id
                    logger.info(f"Created new testbed with ID: {self.testbed_id}")
                else:
                    logger.info(f"Using existing testbed with ID: {self.testbed_id}")

                await self.wait_until_ready()
                return self
            except Exception as e:
                span.record_exception(e)
                raise e

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit the async context manager."""
        if not self.persist_testbed:
            logger.debug(f"Destroying testbed {self.testbed_id} on exit")
            await self.destroy()
        else:
            logger.debug(f"Preserving testbed {self.testbed_id} for future use")

    async def _request(
        self,
        method: str,
        endpoint: str | None = None,
        max_retries: int = 3,
        initial_retry_delay: int = 1,
        max_retry_delay: int = 60,
        operation_timeout: int = 300,
        url: str | None = None,
        **kwargs,
    ) -> Dict[str, Any]:
        url = url or self._get_url(endpoint)
        headers = self._generate_headers()

        retries = 0
        retry_delay = initial_retry_delay
        start_time = time.time()
        last_error = None

        while retries < max_retries:
            current_time = time.time()
            elapsed_time = current_time - start_time

            try:
                # Calculate remaining timeout for this attempt
                remaining_timeout = operation_timeout - elapsed_time
                if remaining_timeout <= 0:
                    raise TestbedTimeoutError(f"Operation timed out after {operation_timeout} seconds")

                # Use the shorter of remaining timeout or 30 seconds per request
                request_timeout = ClientTimeout(total=min(30, remaining_timeout))

                try:
                    async with aiohttp.ClientSession(timeout=request_timeout) as session:
                        async with session.request(method, url, headers=headers, **kwargs) as response:
                            response.raise_for_status()
                            try:
                                return await response.json()
                            except ContentTypeError:
                                text = await response.text()
                                return {"response": text}

                except Exception as e:
                    logger.error(f"Unexpected session error: {str(e)}", exc_info=True)
                    raise

            except Exception as e:
                last_error = e  # Save the last error
                if isinstance(e, asyncio.TimeoutError):
                    logger.warning(
                        f"Request timed out, retrying in {retry_delay} seconds... ({retries + 1}/{max_retries})"
                    )
                elif isinstance(e, ClientConnectionError):
                    logger.warning(
                        f"Connection error ({str(e)}), retrying in {retry_delay} seconds... ({retries + 1}/{max_retries})"
                    )
                elif isinstance(e, ClientResponseError):
                    if 400 <= e.status < 500:
                        error_message = str(e)
                        error_code = None
                        details = {}

                        try:
                            error_data = await response.json()
                            error_message = error_data.get("message", error_message)
                            error_code = error_data.get("error")
                            details = error_data
                        except:
                            try:
                                error_message = await response.text() or error_message
                            except:
                                pass

                        if e.status == 401:
                            raise TestbedAuthenticationError(error_message, error_code, details) from e
                        raise TestbedError(error_message, error_code, details) from e

                    logger.warning(
                        f"Server error {e.status}, retrying in {retry_delay} seconds... ({retries + 1}/{max_retries})"
                    )
                else:
                    logger.warning(
                        f"Unexpected error ({str(e)}), retrying in {retry_delay} seconds... ({retries + 1}/{max_retries})"
                    )

            retries += 1
            if retries < max_retries:
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_retry_delay)

        logger.error(f"All retries exhausted - Last error: {last_error}")

        # Raise appropriate error type based on the last error encountered
        if isinstance(last_error, asyncio.TimeoutError):
            raise TestbedTimeoutError(f"Request to {url} timed out after {max_retries} retries") from last_error
        elif isinstance(last_error, ClientConnectionError):
            raise TestbedConnectionError(f"Failed to connect to testbed after {max_retries} retries") from last_error
        elif isinstance(last_error, ClientResponseError):
            raise TestbedError(f"Server error after {max_retries} retries: {str(last_error)}") from last_error
        else:
            raise TestbedError(
                f"Max retries ({max_retries}) reached for {url} with error: {str(last_error)}"
            ) from last_error

    async def check_health(self, timeout: int = 30) -> bool:
        """Check testbed health status."""
        try:
            response = await self._request("GET", "health", operation_timeout=timeout)
            return response.get("status") == "OK"
        except TestbedError as e:
            if hasattr(e, "response") and e.response.status in [503, 504]:
                return False
            raise

    async def wait_until_ready(self, timeout: float = 600):
        """Wait until testbed is healthy and ready."""
        start_time = time.time()
        wait_time = 0
        while wait_time < timeout:
            wait_time = time.time() - start_time
            try:
                if await self.check_health():
                    logger.debug(f"Testbed {self.testbed_id} is healthy and ready")
                    return True

                logger.debug(f"Testbed {self.testbed_id} not ready yet, waiting...")
                await asyncio.sleep(1)
            except asyncio.TimeoutError as e:
                logger.warning(f"Testbed {self.testbed_id} not ready yet, will retry (Tried for {wait_time} seconds)")
                await asyncio.sleep(1)
            except aiohttp.ClientError as e:
                if isinstance(e, aiohttp.ClientConnectionError):
                    logger.warning(
                        f"Failed to connect to testbed {self.testbed_id}, will retry... (Tried for {wait_time} seconds)"
                    )
                    await asyncio.sleep(1)
                elif getattr(e, "status", None) in [503, 504]:
                    logger.warning(
                        f"Got response {getattr(e, 'status')} indicating that the testbed {self.testbed_id} might not be ready yet, will retry... (Tried for {wait_time} seconds)"
                    )
                    await asyncio.sleep(1)
                else:
                    logger.error(f"Health check failed. {str(e)}")
                    raise
            except Exception as e:
                logger.warning(f"Health check failed: {str(e)}, retrying... (Tried for {wait_time} seconds)")
                await asyncio.sleep(1)

        raise TimeoutError(f"Testbed {self.testbed_id} not ready within {wait_time} seconds")

    async def status(self):
        return await self._request("GET", "status")

    async def get_execution_status(self) -> CommandExecutionResponse:
        try:
            response = await self._request("GET", "exec")
            return CommandExecutionResponse.model_validate(response)
        except Exception as e:
            logger.error(f"Error during get_execution_status: {str(e)}")
            raise

    async def get_diff(self) -> str:
        """Get the current git diff output."""
        try:
            response = await self._request("GET", "diff")
            return response.get("diff", "")
        except Exception as e:
            logger.error(f"Error getting git diff: {str(e)}")
            raise

    async def apply_patch(self, patch: str) -> str:
        if not patch.endswith("\n"):
            patch += "\n"

        response = await self._request("POST", "apply-patch", json={"patch": patch})

        if APPLY_PATCH_FAIL in response.get("output", ""):
            logger.error(f"Failed to apply patch: {patch}.\n\nOutput\n:{response.get('output', '')}")
            raise RuntimeError(f"Failed to apply patch: {patch}.\n\nOutput\n:{response.get('output', '')}")

        diff = await self.get_diff()
        logger.debug(f"Diff after patch: \n{diff}")
        return diff

    async def run_tests(
        self,
        test_files: list[str] | None = None,
        patch: str | None = None,
        timeout: int = 1200,
    ) -> TestRunResponse:
        with tracer.start_as_current_span("run_tests") as span:
            span.set_attribute("test_files", test_files)
            span.set_attribute("has_patch", patch is not None)
            span.set_attribute("timeout", timeout)

            try:
                logger.debug(f"Executing run_tests with test_files={test_files} and patch={patch}")

                if self.test_cache is not None:
                    cache_key = self._generate_cache_key(test_files, patch)
                    cached_result = self.test_cache.get(cache_key)
                    if cached_result:
                        span.set_attribute("cache_hit", True)
                        logger.info("Returning cached test results")
                        return cached_result

                span.set_attribute("cache_hit", False)
                if self.persist_testbed:
                    await self.reset_testbed()
                if patch:
                    await self.apply_patch(patch)

                test_results = []
                if test_files:  # Check if test_files is not None
                    for test_file in test_files:
                        with tracer.start_as_current_span("run_single_test") as test_span:
                            test_span.set_attribute("test_file", test_file)
                            test_start = time.time()
                            data = {"test_files": [test_file]}
                            await self._request("POST", "run-tests", json=data)
                            response = await self.get_execution_status()
                            start_time = time.time()
                            while response.status == "running":
                                if time.time() - start_time > timeout:
                                    raise TimeoutError(f"Execution timed out after {timeout} seconds")
                                await asyncio.sleep(1.0)
                                response = await self.get_execution_status()

                            test_end = time.time()
                            test_duration = test_end - test_start
                            test_span.set_attribute("duration", test_duration)

                            if self.log_dir:
                                datetime_str = time.strftime("%Y%m%d-%H%M%S")
                                with open(f"{self.log_dir}/{datetime_str}_run_tests.log", "a") as f:
                                    f.write(f"Response:\n{response.output}\n")

                            log = response.output.split(f"{RUN_TESTS}\n")[-1]
                            test_result = parse_log(log, self.test_spec.repo)

                            if len(test_result) == 1 and test_result[0].status == TestStatus.ERROR:
                                test_result[0].file_path = test_file
                                test_span.set_attribute("error", True)

                            filtered_test_result = []
                            for test in test_result:
                                if test.file_path != test_file:
                                    logger.info(
                                        f"Skipping test {test.method} in {test.file_path}. Expected {test_file}"
                                    )
                                else:
                                    filtered_test_result.append(test)

                            test_results.extend(filtered_test_result)
                            test_span.set_attribute("test_count", len(filtered_test_result))
                            logger.info(
                                f"Finished running {test_file} tests. Got {len(filtered_test_result)} test results."
                            )

                filtered_test_result = []
                tests_by_file = {}

                for test in test_results:
                    if test.method in self.ignored_tests.get(test.file_path, []):
                        continue

                    filtered_test_result.append(test)

                    if test.file_path not in tests_by_file:
                        tests_by_file[test.file_path] = {"count": 0, "statuses": {}}

                    tests_by_file[test.file_path]["count"] += 1
                    if test.status not in tests_by_file[test.file_path]["statuses"]:
                        tests_by_file[test.file_path]["statuses"][test.status] = 0
                    tests_by_file[test.file_path]["statuses"][test.status] += 1

                summary = [
                    f"{file_path}: {stats['count']} tests. {stats['statuses']}"
                    for file_path, stats in tests_by_file.items()
                ]
                logger.info(f"Test summary by file: {' | '.join(summary)}")

                result = TestRunResponse(test_results=filtered_test_result)

                if self.test_cache is not None:
                    cache_key = self._generate_cache_key(test_files, patch)
                    self.test_cache[cache_key] = result

                return result
            except Exception as e:
                span.record_exception(e)
                raise

    async def run_evaluation(self, run_id: str | None = None, patch: str | None = None) -> EvaluationResult:
        with tracer.start_as_current_span("run_evaluation") as span:
            span.set_attribute("run_id", run_id)
            span.set_attribute("has_patch", patch is not None)

            try:
                self.current_span_id = uuid.uuid4().hex[:16]
                eval_start = time.time()

                if not self.instance:
                    raise ValueError("SWE-bench instance not set")

                try:
                    if self.persist_testbed:
                        await self.reset_testbed()

                    if not patch:
                        logger.info(f"Running evaluation for instance {self.instance.instance_id} with gold prediction")
                        patch = self.instance.patch
                    else:
                        logger.info(f"Running evaluation for instance {self.instance.instance_id} with patch")

                    await self.wait_until_ready()
                    await self.apply_patch(patch)

                    try:
                        git_diff_output_before = (await self.get_diff()).strip()
                    except Exception as e:
                        logger.warning(f"Failed to get git diff before running eval script: {e}")
                        git_diff_output_before = None

                    await self._request("POST", "run-evaluation")

                    response = await self.get_execution_status()
                    while response.status == "running":
                        response = await self.get_execution_status()
                        await asyncio.sleep(1)

                    eval_end = time.time()
                    eval_duration = eval_end - eval_start
                    span.set_attribute("duration", eval_duration)

                    if self.log_dir:
                        datetime_str = time.strftime("%Y%m%d-%H%M%S")
                        with open(f"{self.log_dir}/{datetime_str}_run_tests.log", "a") as f:
                            f.write(f"Response:\n{response.output}\n")

                    if "APPLY_PATCH_FAIL" in response.output:
                        logger.error("Failed to apply patch")
                        span.set_attribute("error", True)
                        span.set_attribute("error.type", "patch_failure")
                        return EvaluationResult(
                            status="error",
                            output=response.get("output", ""),
                        )

                    try:
                        git_diff_output_after = (await self.get_diff()).strip()
                        if git_diff_output_before and git_diff_output_after != git_diff_output_before:
                            logger.info(f"Git diff changed after running eval script")
                            span.set_attribute("git_diff_changed", True)
                    except Exception as e:
                        logger.warning(f"Failed to get git diff after running eval script: {e}")

                    test_status = self.test_spec.get_pred_report(response.output)
                    span.set_attribute("resolved_status", test_status.status)

                    return EvaluationResult(
                        run_id=run_id or "default",
                        resolved=test_status.status == ResolvedStatus.FULL,
                        patch_applied=True,
                        instance_id=self.instance.instance_id,
                        output=response.output,
                        tests_status=test_status,
                    )
                finally:
                    self.current_span_id = None
            except Exception as e:
                span.record_exception(e)
                raise

    async def execute(self, commands: List[str] | str):
        if isinstance(commands, str):
            commands = [commands]

        try:
            response = await self._request("POST", "exec", json={"commands": commands})
            return CommandExecutionResponse.model_validate(response)
        except Exception as e:
            logger.error(f"Error during execute: {str(e)}")
            raise

    async def reset_testbed(self):
        try:
            await self._request("POST", "reset")
        except Exception as e:
            logger.error(f"Error during reset: {str(e)}")
            raise

    async def destroy(self):
        """Destroy the testbed and remove from tracking"""
        try:
            await self._request("DELETE")
        except Exception as e:
            logger.error(f"Error destroying testbed: {e}")
            raise

    async def get_or_create_testbed_async(self) -> TestbedSummary:
        if not self.instance:
            raise TestbedValidationError("instance_id is required")

        url = f"{self.base_url}/testbeds"

        data = {"instance_id": self.instance.instance_id, "run_id": self.run_id}
        response = await self._request("POST", url=url, json=data)
        return TestbedSummary(**response)
