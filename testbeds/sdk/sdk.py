import json
import logging
import os
from typing import Optional, List, Dict, Any
from datetime import datetime
import time
from pathlib import Path

import requests
import aiohttp
import asyncio

from testbeds.schema import (
    TestbedSummary,
    TestbedDetailed,
    SWEbenchInstance,
)
from testbeds.sdk.client import TestbedClient
from testbeds.sdk.exceptions import (
    TestbedError,
    TestbedConnectionError,
    TestbedAuthenticationError,
    TestbedTimeoutError,
    TestbedValidationError,
)
from testbeds.sdk.async_client import AsyncTestbedClient

from opentelemetry import trace

tracer = trace.get_tracer(__name__)
logger = logging.getLogger(__name__)

INSTANCE_CACHE = {}


class TestbedSDK:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        enable_cache: bool = False,
    ):
        base_url = base_url or os.getenv("TESTBED_BASE_URL")
        api_key = api_key or os.getenv("TESTBED_API_KEY")
        assert base_url, "TESTBED_BASE_URL environment variable must be set"
        assert api_key, "TESTBED_API_KEY environment variable must be set"

        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.headers = {"X-API-Key": self.api_key, "Content-Type": "application/json"}
        self.enable_cache = enable_cache
        self._test_cache = {} if enable_cache else None

    def list_testbeds(self) -> List[TestbedSummary]:
        response = self._make_request("GET", "testbeds")
        return [TestbedSummary(**item) for item in response.json()]

    def get_or_create_testbed(self, instance_id: str, run_id: str = "default") -> TestbedSummary:
        if not instance_id:
            raise TestbedValidationError("instance_id is required")

        data = {"instance_id": instance_id, "run_id": run_id}
        logger.info(f"Creating testbed for instance {instance_id} with run_id {run_id}")
        response = self._make_request("POST", "testbeds", json=data)
        return TestbedSummary(**response.json())

    def create_client(
        self,
        instance_id: str | None = None,
        instance: dict | SWEbenchInstance | None = None,
        dataset_name: str | None = None,
        log_dir: str = None,
        run_id: str = "default",
    ) -> TestbedClient:
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
            log_dir=log_dir,
            run_id=run_id,
            base_url=self.base_url,
            api_key=self.api_key,
            test_cache=self._test_cache if self.enable_cache else None,
        )

    async def create_async_client(
        self,
        instance_id: str | None = None,
        instance: dict | SWEbenchInstance | None = None,
        dataset_name: str | None = None,
        log_dir: str = None,
        run_id: str = "default",
        testbed_id: str | None = None,
    ) -> AsyncTestbedClient:
        """Create an async testbed client."""
        with tracer.start_as_current_span("create_async_client") as span:
            span.set_attribute("instance_id", instance_id)
            span.set_attribute("run_id", run_id)
            span.set_attribute("testbed_id", testbed_id)
            logger.info(
                f"Creating async testbed client for {'testbed ' + testbed_id if testbed_id else 'instance ' + str(instance_id)} with run_id {run_id}"
            )

            if not instance_id and not instance and not testbed_id:
                span.set_attribute("error", True)
                raise TestbedValidationError("At least one of instance_id, instance, or testbed_id is required")

            if instance and isinstance(instance, dict):
                instance = SWEbenchInstance.model_validate(instance)
            elif instance_id:
                instance = await self._load_instance(instance_id)
            elif not testbed_id:
                raise ValueError("Either instance_id, instance, or testbed_id must be provided")

            client = AsyncTestbedClient(
                instance=instance,
                dataset_name=dataset_name,
                log_dir=log_dir,
                run_id=run_id,
                base_url=self.base_url,
                api_key=self.api_key,
                test_cache=self._test_cache if self.enable_cache else None,
                testbed_id=testbed_id,
                persist_testbed=testbed_id is not None,
            )

            return client

    def get_testbed(self, testbed_id: str, run_id: str = "default") -> Optional[TestbedDetailed]:
        try:
            response = self._make_request("GET", f"testbeds/{testbed_id}", params={"run_id": run_id})
            return TestbedDetailed(**response.json())
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                return None
            raise

    def delete_testbed(self, testbed_id: str, run_id: str = "default"):
        self._make_request("DELETE", f"testbeds/{testbed_id}", params={"run_id": run_id})

    def delete_all_testbeds(self):
        self._make_request("DELETE", "testbeds")

    def cleanup_user_resources(self):
        self._make_request("POST", "cleanup")

    async def _load_instance(self, instance_id: str) -> SWEbenchInstance:
        """Load a SWEbench instance from the API."""
        global INSTANCE_CACHE
        if instance_id in INSTANCE_CACHE:
            return INSTANCE_CACHE[instance_id]

        # Try to load from individual instance file first
        instance_path = Path("/app/instances") / f"{instance_id}.json"
        if instance_path.exists():
            logger.debug(f"Loading instance from file: {instance_path.absolute()}")
            try:
                with instance_path.open("r", encoding="utf-8") as f:
                    instance_data = json.load(f)
                    INSTANCE_CACHE[instance_id] = SWEbenchInstance.model_validate(instance_data)
                    return INSTANCE_CACHE[instance_id]
            except Exception as e:
                logger.warning(f"Failed to load instance from file {instance_path}: {e}")

        # If file doesn't exist or loading failed, try API
        logger.info(f"Instance {instance_id} not found in local file, trying to load from API")
        try:
            response = await self._make_async_request("GET", f"instances/{instance_id}")
            INSTANCE_CACHE[instance_id] = SWEbenchInstance.model_validate(response)
            return INSTANCE_CACHE[instance_id]
        except Exception as e:
            logger.error(f"Failed to load instance {instance_id} from API: {e}")
            raise TestbedError(f"Failed to load instance {instance_id}") from e

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
                error_message = error_data.get("message")
                error_code = error_data.get("error")
                details = error_data
            except (ValueError, AttributeError):
                error_message = e.response.text or str(e)

            if 400 <= e.response.status_code < 500:
                if e.response.status_code == 401:
                    raise TestbedAuthenticationError(error_message, error_code, details) from e
            raise TestbedError(error_message, error_code, details) from e

        except Exception as e:
            raise TestbedError(f"Unexpected error: {str(e)}") from e

    async def _make_async_request(
        self,
        method: str,
        endpoint: str,
        max_retries: int = 3,
        initial_retry_delay: int = 1,
        max_retry_delay: int = 60,
        **kwargs,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}/{endpoint}"
        retries = 0
        retry_delay = initial_retry_delay
        logger.info(f"Making async request to {url} with method {method}")

        while retries < max_retries:
            try:
                timeout = aiohttp.ClientTimeout(total=30)  # Match the sync version's timeout
                logger.debug(f"Attempt {retries + 1}/{max_retries} with timeout {timeout.total}s")
                start_time = time.time()

                logger.debug(f"Creating client session at {datetime.now().isoformat()}")
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    logger.debug(f"Session created after {time.time() - start_time:.2f}s")
                    logger.debug(f"Initiating {method} request to {url}")

                    async with session.request(method, url, headers=self.headers, **kwargs) as response:
                        conn_time = time.time() - start_time
                        logger.debug(f"Connection established after {conn_time:.2f}s")
                        logger.debug(f"Response status: {response.status}, headers: {response.headers}")

                        response.raise_for_status()
                        json_start = time.time()
                        result = await response.json()
                        json_time = time.time() - json_start
                        total_time = time.time() - start_time

                        logger.debug(
                            f"Request timing breakdown - Connection: {conn_time:.2f}s, JSON parsing: {json_time:.2f}s, Total: {total_time:.2f}s"
                        )
                return result

            except aiohttp.ClientConnectionError as e:
                logger.warning(f"Connection error on attempt {retries + 1}: {str(e)}")
                logger.info(f"Connection error details: {type(e).__name__}, {str(e)}")
                retries += 1
                if retries == max_retries:
                    raise TestbedConnectionError(f"Failed to connect to testbed: {str(e)}") from e
                logger.info(f"Retrying in {retry_delay}s...")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_retry_delay)
            except asyncio.TimeoutError as e:
                elapsed = time.time() - start_time
                logger.warning(f"Timeout error on attempt {retries + 1} after {elapsed:.2f}s")
                logger.info(f"Timeout details: Limit was {timeout.total}s, actual time was {elapsed:.2f}s")
                retries += 1
                if retries == max_retries:
                    raise TestbedTimeoutError(f"Request timed out: {str(e)}") from e
                logger.info(f"Retrying in {retry_delay}s...")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_retry_delay)
            except aiohttp.ClientResponseError as e:
                logger.warning(f"Response error on attempt {retries + 1}: {str(e)}")
                logger.info(f"Response error details - Status: {getattr(e, 'status', 'unknown')}, Message: {str(e)}")
                error_code = None
                details = {}

                try:
                    error_data = await response.json()
                    error_message = error_data.get("message")
                    error_code = error_data.get("error")
                    details = error_data
                except:
                    error_message = await response.text() or str(e)

                if 400 <= e.status < 500:
                    if e.status == 401:
                        raise TestbedAuthenticationError(error_message, error_code, details) from e
                    raise TestbedError(error_message, error_code, details) from e

                retries += 1
                if retries == max_retries:
                    raise TestbedError(error_message, error_code, details) from e
                logger.info(f"Retrying in {retry_delay}s...")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_retry_delay)
            except Exception as e:
                logger.error(
                    f"Unexpected error on attempt {retries + 1}: {str(e)}",
                    exc_info=True,
                )
                raise TestbedError(f"Unexpected error: {str(e)}") from e

        raise TestbedError(f"Max retries reached for {url}")

    async def get_or_create_testbed_async(self, instance_id: str, run_id: str = "default") -> TestbedSummary:
        if not instance_id:
            raise TestbedValidationError("instance_id is required")

        data = {"instance_id": instance_id, "run_id": run_id}
        response = await self._make_async_request("POST", "testbeds", json=data)
        return TestbedSummary(**response)

    def clear_cache(self):
        """Clear the test results cache"""
        if self._test_cache is not None:
            self._test_cache.clear()

    def __del__(self):
        """Cleanup when SDK is deleted"""
        self.clear_cache()
