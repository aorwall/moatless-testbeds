import base64
import json
import logging
import os
import re
import time
import asyncio
from typing import Optional, List, Dict, Any, cast, Union

import aiohttp
from aiohttp import ClientTimeout, ClientResponseError
from kubernetes_asyncio import client, config
from kubernetes_asyncio.client.models.v1_pod import V1Pod
from kubernetes_asyncio.client.models.v1_pod_list import V1PodList
from kubernetes_asyncio.client.models.v1_container_status import V1ContainerStatus
from kubernetes_asyncio.client.rest import ApiException
from fastapi import HTTPException

from testbeds.exceptions import TestbedBadRequestError
from testbeds.schema import (
    RunCommandsRequest,
    CommandExecutionResponse,
    TestbedDetailed,
    TestbedStatusDetailed,
    ContainerStatus,
)
from testbeds.swebench.constants import APPLY_PATCH_FAIL, FAIL_TO_PASS, PASS_TO_PASS
from testbeds.swebench.test_spec import TestSpec
from testbeds.swebench.utils import load_swebench_instance

logger = logging.getLogger(__name__)


class TestbedClient:
    def __init__(
        self,
        testbed_id: str,
        instance_id: str,
        base_url: str,
        namespace: str = "testbed-dev",
        testbed_namespace: str = "testbed-dev",
        test_spec: TestSpec | None = None,
        startup_timeout=600,
        ignored_tests: dict[str, list[str]] = {},
        in_cluster: bool = False,
        session: aiohttp.ClientSession | None = None,
    ):
        assert testbed_id, "Testbed ID is required"

        self.testbed_id = testbed_id
        self.namespace = namespace
        self.testbed_namespace = testbed_namespace

        if not base_url:
            self.core_v1: Optional[client.CoreV1Api] = None
            self.batch_v1: Optional[client.BatchV1Api] = None
        else:
            self.core_v1 = None
            self.batch_v1 = None

        self._base_url = base_url
        self._session = session

        self.ignored_tests = ignored_tests

        self.instance_id = instance_id
        self.test_spec = test_spec
        self.startup_timeout = startup_timeout

        self.in_cluster = in_cluster
        self._own_session = False

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get the aiohttp session, creating a new one if needed."""
        if self._session and not self._session.closed:
            return self._session

        # Create our own session if needed
        self._session = aiohttp.ClientSession()
        self._own_session = True
        return self._session

    async def cleanup(self):
        """Cleanup resources - close sessions and clients"""
        try:
            if self.batch_v1:
                response = await self.batch_v1.delete_namespaced_job(
                    name=self.testbed_id,
                    namespace=self.testbed_namespace,
                    body=client.V1DeleteOptions(
                        propagation_policy="Foreground", grace_period_seconds=0
                    ),
                )

                if self.core_v1:
                    await self.core_v1.delete_namespaced_service(
                        name=self.testbed_id,
                        namespace=self.testbed_namespace,
                        body=client.V1DeleteOptions(
                            propagation_policy="Foreground", grace_period_seconds=0
                        ),
                    )

                return response

        except client.ApiException as e:
            if e.status == 404:
                logger.warning(f"Job {self.testbed_id} not found.")
            else:
                error_message = f"Error deleting job {self.testbed_id}: {str(e)}"
                logger.exception(error_message)
                raise RuntimeError(error_message)
        except Exception as e:
            error_message = (
                f"Unexpected error during cleanup of job {self.testbed_id}: {str(e)}"
            )
            logger.exception(error_message)
            raise RuntimeError(error_message)

        finally:
            if self.core_v1:
                await self.core_v1.api_client.close()
            if self.batch_v1:
                await self.batch_v1.api_client.close()
            # Only close the session if we created it
            if self._session and self._own_session and not self._session.closed:
                await self._session.close()

    async def _get_test_spec(self) -> Optional[TestSpec]:
        if not self.test_spec:
            try:
                instance = await load_swebench_instance(self.instance_id)
                if instance:
                    self.test_spec = TestSpec.from_instance(instance)
            except Exception as e:
                logger.error(f"Error loading test spec: {str(e)}")
                return None

        return self.test_spec

    @property
    def base_url(self) -> str | None:
        return self._base_url

    async def check_health(self, timeout: int = 60) -> dict:
        """Check the health of the testbed with improved timeout handling.

        Args:
            timeout: Number of seconds to wait for response before timing out

        Returns:
            dict: Health status information
        """
        try:
            session = await self._get_session()
            if session.closed:
                logger.warning("Session was closed, creating new one")
                self._session = None
                session = await self._get_session()

            timeout_obj = ClientTimeout(total=timeout, connect=timeout / 3)
            logger.debug(
                f"Checking health for testbed {self.testbed_id} at {self.base_url}/health"
            )

            try:
                async with session.get(
                    f"{self.base_url}/health",
                    timeout=timeout_obj,
                    headers={
                        "Connection": "close"
                    },  # Ensure connection is closed after request
                ) as response:
                    response.raise_for_status()
                    return await response.json()
            except asyncio.TimeoutError as e:
                logger.warning(
                    f"Timeout ({timeout}s) reached while checking health for testbed {self.testbed_id}"
                )
                # If we created this session and got a timeout, clean it up
                if self._own_session:
                    await session.close()
                    self._session = None
                return {
                    "status": "timeout",
                    "message": f"Health check timed out after {timeout} seconds",
                }
            except aiohttp.ClientError as e:
                logger.debug(
                    f"Connection refused to testbed {self.testbed_id} - service likely not ready yet. Error: {str(e)}"
                )
                # If we created this session and got an error, clean it up
                if self._own_session:
                    await session.close()
                    self._session = None
                return {"status": "unavailable", "message": "Service not ready"}

        except Exception as e:
            logger.error(
                f"Error checking health for testbed {self.testbed_id}: {str(e)}"
            )
            # Clean up session on unexpected errors
            if self._own_session and self._session and not self._session.closed:
                await self._session.close()
                self._session = None
            raise

    async def get_testbed(self) -> Optional[TestbedDetailed]:
        job = await self._get_job()
        if job:
            status = await self._read_testbed_status_detailed(job.metadata.name)
            if status:
                external_ip = None
                if not self.in_cluster:
                    try:
                        external_ip = await self._get_service_external_ip()
                    except ValueError:
                        logger.debug(
                            f"External IP not yet available for testbed {self.testbed_id}"
                        )

                return TestbedDetailed(
                    testbed_id=job.metadata.name,
                    instance_id=job.metadata.labels.get("instance-id", "unknown"),
                    status=status,
                    external_ip=external_ip,
                )

        return None

    async def _read_testbed_status_detailed(
        self, testbed_id: str
    ) -> Optional[TestbedStatusDetailed]:
        """Read detailed status of the testbed."""
        try:
            if not self.core_v1:
                await self.initialize()

            pod_list_response = await self.core_v1.list_namespaced_pod(
                namespace=self.namespace,
                label_selector=f"job-name={testbed_id}",
            )
            pod_list = cast(V1PodList, pod_list_response)

            if not pod_list or not pod_list.items:
                logger.warning(f"Pod not found for testbed {testbed_id}")
                return None

            pod = pod_list.items[0]
            testbed_status = ContainerStatus(
                ready=False,
                started=False,
                restart_count=0,
                state="unknown",
                reason=None,
                message=None,
                restart_reasons=None,
            )
            sidecar_status = ContainerStatus(
                ready=False,
                started=False,
                restart_count=0,
                state="unknown",
                reason=None,
                message=None,
                restart_reasons=None,
            )

            if pod.status and pod.status.container_statuses:
                for container in pod.status.container_statuses:
                    container = cast(V1ContainerStatus, container)

                    # Fetch logs for containers with restarts to provide context
                    prev_logs = None
                    if container.restart_count > 0:
                        prev_logs = await self._get_container_logs(
                            pod.metadata.name, container.name
                        )

                    status = self._get_container_status(
                        container, pod.metadata.name, prev_logs
                    )

                    if container.name == "testbed":
                        testbed_status = status
                    elif container.name == "sidecar":
                        sidecar_status = status

            return TestbedStatusDetailed(
                pod_phase=pod.status.phase if pod.status else "Unknown",
                testbed=testbed_status,
                sidecar=sidecar_status,
            )
        except Exception as e:
            logger.error(f"Error reading testbed status: {e}")
            return None

    async def _get_service_external_ip(self) -> str:
        """Get the external IP of the service."""
        try:
            if not self.core_v1:
                await self.initialize()
                if not self.core_v1:
                    raise ValueError("Kubernetes client not initialized")

            service = await self.core_v1.read_namespaced_service(
                name=self.testbed_id, namespace=self.testbed_namespace
            )
            if (
                service
                and service.status
                and service.status.load_balancer
                and service.status.load_balancer.ingress
            ):
                return service.status.load_balancer.ingress[0].ip
            raise ValueError(f"No external IP found for testbed {self.testbed_id}")
        except ApiException as e:
            logger.error(f"Error getting service external IP: {e}")
            raise ValueError(
                f"Failed to get external IP for testbed {self.testbed_id}: {e}"
            )

    def _get_container_status(
        self, container, pod_name=None, prev_logs=None
    ) -> ContainerStatus:
        state = "unknown"
        reason = None
        message = None
        restart_reasons = None

        if container.state.running:
            state = "running"
        elif container.state.waiting:
            state = "waiting"
            reason = container.state.waiting.reason
            message = container.state.waiting.message
        elif container.state.terminated:
            state = "terminated"
            reason = container.state.terminated.reason
            message = container.state.terminated.message

        # Add restart reasons info when restart_count > 0
        if container.restart_count > 0 and hasattr(container, "last_state"):
            last_state = container.last_state
            if last_state:
                restart_info = []
                if last_state.terminated:
                    restart_info.append(
                        f"Last terminated: {last_state.terminated.reason or 'Unknown'}"
                    )
                    if last_state.terminated.message:
                        restart_info.append(f"Message: {last_state.terminated.message}")
                    if (
                        hasattr(last_state.terminated, "exit_code")
                        and last_state.terminated.exit_code
                    ):
                        restart_info.append(
                            f"Exit code: {last_state.terminated.exit_code}"
                        )
                elif last_state.waiting:
                    restart_info.append(
                        f"Last waiting: {last_state.waiting.reason or 'Unknown'}"
                    )
                    if last_state.waiting.message:
                        restart_info.append(f"Message: {last_state.waiting.message}")

                if restart_info:
                    restart_reasons = "; ".join(restart_info)

        # Add previous logs to restart_reasons if available
        if prev_logs:
            if restart_reasons:
                restart_reasons = (
                    f"{restart_reasons}\n\nPrevious logs (tail):\n{prev_logs}"
                )
            else:
                restart_reasons = f"Previous logs (tail):\n{prev_logs}"

        return ContainerStatus(
            ready=container.ready,
            started=container.started,
            restart_count=container.restart_count,
            state=state,
            reason=reason,
            message=message,
            restart_reasons=restart_reasons,
        )

    async def _get_job(self):
        """Get the job from Kubernetes API."""
        try:
            if not self.batch_v1:
                await self.initialize()
                if not self.batch_v1:
                    return None

            response = await self.batch_v1.read_namespaced_job(
                name=self.testbed_id, namespace=self.testbed_namespace
            )
            return response
        except ApiException as e:
            if e.status == 404:
                logger.info(
                    f"Job {self.testbed_id} not found in namespace {self.testbed_namespace}."
                )
                return None
            else:
                raise

    async def _execute_command(self, commands: list[str] | str, timeout: int = 60):
        try:
            if isinstance(commands, str):
                commands = commands.split("\n")

            request = RunCommandsRequest(commands=commands, timeout=timeout)
            session = await self._get_session()
            timeout_obj = ClientTimeout(total=timeout)
            async with session.post(
                f"{self.base_url}/exec", json=request.model_dump(), timeout=timeout_obj
            ) as response:
                response.raise_for_status()
                data = await response.json()
                return CommandExecutionResponse.model_validate(data)

        except aiohttp.ClientError as e:
            logger.error(f"Error during execute_commands: {str(e)}")
            raise HTTPException(status_code=502, detail=str(e))

    async def execute(
        self, commands: list[str] | str, timeout: int = 60
    ) -> CommandExecutionResponse:
        logger.debug(f"Executing commands: {commands}")
        response = await self._execute_command(commands, timeout)

        while response.status == "running":
            response = await self.get_execution_status()
            await asyncio.sleep(0.1)

        return response

    async def execute_async(
        self, commands: list[str] | str
    ) -> CommandExecutionResponse:
        return await self._execute_command(commands)

    async def get_execution_status(self) -> CommandExecutionResponse:
        """Get the status of the current command execution."""
        if not self.base_url:
            raise HTTPException(
                status_code=502,
                detail=f"No base URL configured for testbed {self.testbed_id}",
            )

        try:
            return await self._fetch_execution_status_from_api()
        except aiohttp.ClientError as e:
            logger.warning(
                f"Connection error to testbed {self.testbed_id}. Error: {str(e)}"
            )
            return await self._handle_api_connection_error()
        except Exception as e:
            logger.error(f"Error during get_execution_status: {str(e)}")
            raise HTTPException(
                status_code=502,
                detail=f"Request failed for testbed {self.testbed_id}: {str(e)}",
            )

    async def _fetch_execution_status_from_api(self) -> CommandExecutionResponse:
        """Fetch execution status from the testbed API."""
        session = await self._get_session()
        async with session.get(f"{self.base_url}/exec") as response:
            response.raise_for_status()
            data = await response.json()
            response = CommandExecutionResponse.model_validate(data)
            if response.status == "completed":
                logger.info(f"Command execution completed in testbed {self.testbed_id}")
            return response

    async def _handle_api_connection_error(self) -> CommandExecutionResponse:
        """Handle connection errors by checking pod status via Kubernetes API."""
        # Ensure Kubernetes client is initialized
        await self._ensure_kubernetes_client()

        # Get testbed status
        status = await self._get_testbed_status()

        # Always raise an exception with appropriate details
        if status:
            status_json = status.model_dump_json(indent=2)
            logger.warning(
                f"Connection refused to testbed {self.testbed_id}. Status: {status_json}"
            )
            raise HTTPException(
                status_code=502,
                detail=f"Connection refused to testbed {self.testbed_id}. Status: {status_json}",
            )

        # If we reached here, we couldn't get status details
        raise HTTPException(
            status_code=502,
            detail=f"Connection refused to testbed {self.testbed_id}. Status unknown.",
        )

    async def _ensure_kubernetes_client(self):
        """Ensure Kubernetes client is initialized."""
        if not self.core_v1:
            try:
                await self.initialize()
            except Exception as e:
                logger.exception(f"Failed to initialize Kubernetes client: {e}")
                raise HTTPException(
                    status_code=502,
                    detail=f"Connection refused to testbed {self.testbed_id} and failed to initialize Kubernetes client.",
                )

    async def _get_testbed_status(self):
        """Get testbed status via Kubernetes API with error handling."""
        try:
            return await self._read_testbed_status_detailed(self.testbed_id)
        except Exception as e:
            logger.exception(f"Error getting testbed status: {e}")
            raise HTTPException(
                status_code=502,
                detail=f"Connection refused to testbed {self.testbed_id}. Unable to get current status.",
            )

    async def get_diff(self) -> str:
        """Get the current git diff output."""
        try:
            response = await self.execute("git diff")
            return response.output or ""
        except aiohttp.ClientError as e:
            logger.error(f"Error getting git diff: {str(e)}")
            raise HTTPException(status_code=502, detail=str(e))

    async def reset(self):
        test_spec = await self._get_test_spec()
        if not test_spec:
            raise HTTPException(status_code=400, detail="Test spec not available")

        await self.execute(test_spec.reset_commands)
        diff = await self.get_diff()
        logger.debug(f"Diff after patch: \n{diff}")
        return diff

    async def apply_patch(self, patch: str) -> str:
        if not patch:
            logger.warning(
                f"apply_patch() No patch provided to apply on testbed {self.testbed_id}"
            )

        logger.info(f"Applying patch to testbed {self.testbed_id}")
        test_spec = await self._get_test_spec()
        if not test_spec:
            raise HTTPException(status_code=400, detail="Test spec not available")

        patch_files = self._get_patch_files(patch)
        for patch_file in patch_files:
            try:
                file = await self.get_file(patch_file)
                if not file:
                    await self.save_file(patch_file, "")
            except Exception as e:
                logger.exception(f"Failed to check if {patch_file} exists: {e}")

        patch_filepath = "/shared/patch.diff"
        if not patch.endswith("\n"):
            patch += "\n"
        await self.save_file(patch_filepath, patch)
        response = await self.execute(test_spec.patch_commands(patch_filepath))

        if response.output and APPLY_PATCH_FAIL in response.output:
            logger.error(
                f"Failed to apply patch: {patch}.\n\nOutput\n:{response.output}"
            )
            raise HTTPException(
                status_code=400,
                detail=f"Failed to apply patch: {patch}.\n\nOutput\n:{response.output}",
            )

        diff = await self.get_diff()
        logger.debug(f"Diff after patch: \n{diff}")
        return diff

    def _get_patch_files(self, patch: str) -> list:
        diff_pat = r"diff --git a/.* b/(.*)"
        patch_files = re.findall(diff_pat, patch)
        return patch_files

    async def run_tests(
        self, test_files: list[str] | None = None
    ) -> CommandExecutionResponse:
        logger.info(f"run_tests: test_files={test_files}")
        test_spec = await self._get_test_spec()
        if not test_spec:
            raise HTTPException(status_code=400, detail="Test spec not available")

        commands = test_spec.test_script(test_files)
        return await self.execute_async(commands)

    async def run_evaluation(self) -> CommandExecutionResponse:
        test_spec = await self._get_test_spec()
        if not test_spec:
            raise HTTPException(status_code=400, detail="Test spec not available")

        return await self.execute_async(test_spec.eval_script_list)

    async def save_file(self, file_path: str, content: str):
        try:
            encoded_content = base64.b64encode(content.encode()).decode()
            data = {"file_path": file_path, "content": encoded_content}
            logger.debug(f"Saving file: {file_path}")
            session = await self._get_session()
            timeout_obj = ClientTimeout(total=30)
            async with session.post(
                f"{self.base_url}/file", json=data, timeout=timeout_obj
            ) as response:
                response.raise_for_status()
                return await response.json()
        except aiohttp.ClientError as e:
            logger.error(f"Error saving file {file_path}: {str(e)}")
            raise HTTPException(status_code=502, detail=str(e))

    async def get_file(self, file_path: str):
        try:
            session = await self._get_session()
            async with session.get(
                f"{self.base_url}/file", params={"file_path": file_path}
            ) as response:
                if response.status == 404:
                    return None

                response.raise_for_status()
                data = await response.json()
                if "content" in data:
                    return base64.b64decode(data["content"]).decode()
                else:
                    return data
        except aiohttp.ClientResponseError as e:
            if e.status == 404:
                return None

            logger.error(f"Error getting file: {str(e)}")
            return {"error": str(e)}
        except aiohttp.ClientError as e:
            logger.error(f"Error getting file: {str(e)}")
            return {"error": str(e)}

    async def initialize(self) -> None:
        """Initialize Kubernetes clients"""
        try:
            if self.core_v1 is not None and self.batch_v1 is not None:
                return

            try:
                if self.in_cluster:
                    config.load_incluster_config()
                    logger.info("Loaded in-cluster Kubernetes configuration.")
                else:
                    await config.load_kube_config()
                    logger.info("Loaded local Kubernetes configuration.")
            except config.ConfigException:
                logger.error("Failed to load Kubernetes configuration")
                raise

            api_client = client.ApiClient()
            self.core_v1 = client.CoreV1Api(api_client)
            self.batch_v1 = client.BatchV1Api(api_client)
        except Exception as e:
            logger.error(f"Failed to initialize Kubernetes clients: {e}")
            raise RuntimeError("Failed to initialize Kubernetes clients") from e

    async def _get_container_logs(
        self, pod_name: str, container_name: str, lines: int = 10
    ) -> Optional[str]:
        """Get the most recent logs for a container to provide context for restarts."""
        try:
            if not self.core_v1:
                await self.initialize()
                if not self.core_v1:
                    return None

            logs = await self.core_v1.read_namespaced_pod_log(
                name=pod_name,
                namespace=self.namespace,
                container=container_name,
                tail_lines=lines,
                previous=True,  # Get logs from the previous instance of the container
            )
            return logs
        except ApiException as e:
            # 400 error is expected if the container has not been restarted yet
            if e.status == 400:
                return None
            logger.debug(
                f"Error retrieving logs for container {container_name} in pod {pod_name}: {e}"
            )
            return None
        except Exception as e:
            logger.debug(f"Unexpected error retrieving logs: {e}")
            return None
