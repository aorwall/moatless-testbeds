import json
import logging
import os
import random
import string
import time
import asyncio
from collections import namedtuple
from typing import Optional, List, cast, Any, Dict, Union

import yaml
from jinja2 import Environment, FileSystemLoader
from kubernetes_asyncio import client, config
from kubernetes_asyncio.client.models.v1_job import V1Job
from kubernetes_asyncio.client.models.v1_service import V1Service
from kubernetes_asyncio.client.models.v1_pod import V1Pod
from kubernetes_asyncio.client.models.v1_container_status import V1ContainerStatus
from kubernetes_asyncio.client.rest import ApiException
from kubernetes_asyncio.client.models.v1_job_list import V1JobList
from kubernetes_asyncio.client.models.v1_service_list import V1ServiceList
from kubernetes_asyncio.client.models.v1_pod_list import V1PodList

from testbeds.api.client import TestbedClient
from testbeds.exceptions import TestbedNotFoundError
from testbeds.schema import (
    TestbedStatusDetailed,
    ContainerStatus,
    TestbedSummary,
    TestbedDetailed,
    SWEbenchInstance,
)
from testbeds.swebench.test_spec import TestSpec
from testbeds.swebench.utils import load_swebench_instance
import aiohttp

KUBE_NAMESPACE = os.getenv("KUBE_NAMESPACE", "testbeds")
SWEBENCH_DOCKER_REGISTRY = os.getenv("SWEBENCH_DOCKER_REGISTRY", "swebench")
SWEBENCH_IMAGE_PREFIX = os.getenv("SWEBENCH_IMAGE_PREFIX", "sweb.eval.x86_64.")

SWE_GYM_DOCKER_REGISTRY = os.getenv("SWE_GYM_DOCKER_REGISTRY", "xingyaoww")
SWE_GYM_IMAGE_PREFIX = os.getenv("SWE_GYM_IMAGE_PREFIX", "sweb.eval.x86_64.")

logger = logging.getLogger(__name__)
logging.getLogger("azure").setLevel(logging.WARNING)

ExecResult = namedtuple("ExecResult", "exit_code,output")


high_cpu_instances = [
    "sympy__sympy-11870",
    "sympy__sympy-13437",
    "matplotlib__matplotlib-24149",
    "matplotlib__matplotlib-23314",
    "matplotlib__matplotlib-24334",
    "matplotlib__matplotlib-24149",
    "matplotlib__matplotlib-26011",
    "mwaskom__seaborn-3407",
    "sympy__sympy-16988",
    "sympy__sympy-17139",
    "sympy__sympy-18057",
    "sympy__sympy-18199",
]


class TestbedManager:
    def __init__(
        self,
        namespace: str = KUBE_NAMESPACE,
    ):
        self.namespace = namespace
        self.container_name = "testbed"
        self.in_cluster = False
        self._core_v1: Optional[client.CoreV1Api] = None
        self._batch_v1: Optional[client.BatchV1Api] = None
        self._api_client: Optional[client.ApiClient] = None
        self._session: Optional[aiohttp.ClientSession] = None

        # Jinja2 environment setup
        self.template_dir = os.path.join(os.path.dirname(__file__), "template")
        self.template_file = "pod_template.yaml"
        self.env = Environment(loader=FileSystemLoader(self.template_dir))
        self.job_template = self.env.get_template("pod_template.yaml")
        self.service_template = self.env.get_template("service_template.yaml")
        self.ignored_tests = self.create_ignored_tests_dataset()

    @property
    def core_v1(self) -> client.CoreV1Api:
        if not self._core_v1:
            raise RuntimeError("CoreV1Api not initialized")
        return self._core_v1

    @property
    def batch_v1(self) -> client.BatchV1Api:
        if not self._batch_v1:
            raise RuntimeError("BatchV1Api not initialized")
        return self._batch_v1

    @property
    def api_client(self) -> client.ApiClient:
        if not self._api_client:
            raise RuntimeError("ApiClient not initialized")
        return self._api_client

    async def set_session(self, session: aiohttp.ClientSession) -> None:
        """Set the shared session to be used by all clients."""
        self._session = session

    async def initialize(self) -> None:
        """Initialize Kubernetes clients"""
        try:
            if self._api_client is not None:
                return

            try:
                if os.getenv("KUBERNETES_SERVICE_HOST"):
                    config.load_incluster_config()
                    self.in_cluster = True
                    logger.info("Loaded in-cluster Kubernetes configuration.")
                else:
                    await config.load_kube_config()
                    self.in_cluster = False
                    logger.info("Loaded local Kubernetes configuration.")
            except config.ConfigException as e:
                logger.error(f"Failed to load Kubernetes config: {e}")
                raise

            self._api_client = client.ApiClient()
            self._core_v1 = client.CoreV1Api(self._api_client)
            self._batch_v1 = client.BatchV1Api(self._api_client)
        except Exception as e:
            logger.error(f"Failed to initialize Kubernetes clients: {e}")
            raise RuntimeError("Failed to initialize Kubernetes clients") from e

    async def close(self):
        """Close the API client and clean up resources"""
        if self._api_client:
            await self._api_client.close()
            self._api_client = None
            self._core_v1 = None
            self._batch_v1 = None

        # Don't close the session here since it's managed by FastAPI

    async def ensure_initialized(self) -> None:
        """Ensure Kubernetes clients are initialized"""
        if self._api_client is None:
            await self.initialize()
            if self._api_client is None:
                raise RuntimeError("Failed to initialize Kubernetes clients")

    def create_ignored_tests_dataset(self):
        file_path = os.path.join(os.path.dirname(__file__), f"tests.json")
        if not os.path.exists(file_path):
            logger.info(f"Tests file not found on path {file_path}")
            return {}
        with open(file_path) as f:
            dataset = json.load(f)

        ignored_tests = {}
        for instance in dataset:
            instance_id = instance["instance_id"]
            ignored_tests[instance_id] = {}
            for file_path, tests in instance["tests"].items():
                ignored_tests[instance_id][file_path] = [
                    test["method"]
                    for test in tests
                    if test["status"] in ["FAILED", "ERROR"]
                ]

        return ignored_tests

    async def list_testbeds(self, user_id: str) -> List[TestbedSummary]:
        await self.ensure_initialized()
        testbeds = []
        job_list_response = await self.batch_v1.list_namespaced_job(
            namespace=self.namespace
        )
        job_list = cast(V1JobList, job_list_response)

        if job_list and job_list.items:
            for job in job_list.items:
                if (
                    job.metadata
                    and job.metadata.labels
                    and job.metadata.labels.get("user-id") == user_id
                ):
                    status = await self._read_testbed_status(job.metadata.name)
                    testbeds.append(
                        TestbedSummary(
                            testbed_id=job.metadata.name,
                            instance_id=job.metadata.labels.get(
                                "instance-id", "unknown"
                            ),
                            status=status,
                        )
                    )
        return testbeds

    async def get_or_create_testbed(
        self,
        instance_id: str,
        user_id: str = "default",
        timeout: int = 60,
        run_id: Optional[str] = None,
    ) -> Optional[TestbedSummary]:
        await self.ensure_initialized()
        run_id = run_id or "default"

        logger.info(
            f"get_or_create_testbed(user: {user_id}, instance_id: {instance_id}, run_id: {run_id})"
        )

        # Wait for any deletion to complete before creating a new job
        start_time = time.time()
        while True:
            if time.time() - start_time > timeout:
                raise TimeoutError(
                    "Timed out waiting for previous job deletion to complete"
                )

            try:
                job_list_response = await self.batch_v1.list_namespaced_job(
                    namespace=self.namespace
                )
                job_list = cast(V1JobList, job_list_response)
                found_job = None

                if job_list and job_list.items:
                    for job in job_list.items:
                        if (
                            job.metadata
                            and job.metadata.labels
                            and job.metadata.labels.get("instance-id") == instance_id
                            and job.metadata.labels.get("user-id") == user_id
                            and job.metadata.labels.get("run-id") == run_id
                        ):
                            # Check if job is being deleted
                            if job.metadata.deletion_timestamp:
                                await asyncio.sleep(0.5)
                                continue
                            found_job = job
                            break

                if found_job:
                    status = await self._read_testbed_status(found_job.metadata.name)
                    logger.info(
                        f"Found existing testbed job {found_job.metadata.name} with status {status}"
                    )
                    return TestbedSummary(
                        testbed_id=found_job.metadata.name,
                        instance_id=found_job.metadata.labels.get(
                            "instance-id", "unknown"
                        ),
                        status=status,
                        run_id=run_id,
                    )
                break  # No existing job found, exit loop

            except ApiException as e:
                logger.warning(f"API error while checking jobs: {e}")
                await asyncio.sleep(0.5)

        return await self.create_testbed(instance_id, user_id, timeout, run_id)

    async def create_testbed(
        self,
        instance_id: str,
        user_id: str = "default",
        timeout: int = 60,
        run_id: Optional[str] = None,
    ) -> TestbedSummary:
        await self.ensure_initialized()
        run_id = run_id or "default"

        logger.info(
            f"create_testbed(user: {user_id}, instance_id: {instance_id}, run_id: {run_id}) creating new testbed."
        )
        start_time = time.time()
        try:
            instance = await load_swebench_instance(instance_id)
            if not instance:
                logger.error(f"Instance {instance_id} not found")
                raise ValueError(f"Instance {instance_id} not found")
        except Exception as e:
            logger.exception(f"Error loading instance {instance_id}")
            raise RuntimeError(f"Error loading instance {instance_id}")

        try:
            testbed_id = self._generate_test_id(instance_id, user_id, run_id)

            job_manifest = self._create_job_manifest(
                instance=instance, user_id=user_id, testbed_id=testbed_id, run_id=run_id
            )
            job_response = await self.batch_v1.create_namespaced_job(
                body=job_manifest, namespace=self.namespace
            )
            job = cast(V1Job, job_response)
            logger.info(f"Created job for {testbed_id}")

            # Only create service if not in cluster
            if not self.in_cluster:
                service_manifest = self._create_service_manifest(
                    testbed_id, user_id, run_id, instance_id
                )
                service_response = await self.core_v1.create_namespaced_service(
                    body=service_manifest, namespace=self.namespace
                )
                service = cast(V1Service, service_response)
                logger.info(f"Created service for {testbed_id}")

            # Wait for job and optionally service to be created
            while True:
                if time.time() - start_time > timeout:
                    raise TimeoutError(
                        f"Creation of job or service {testbed_id} timed out"
                    )

                job = await self._get_job(testbed_id)
                if not self.in_cluster:
                    service = await self._get_service(testbed_id)
                    if job and service:
                        break
                else:
                    if job:
                        break

                await asyncio.sleep(0.1)

            if not job or not job.metadata or not job.metadata.labels:
                raise RuntimeError(f"Failed to create job {testbed_id}")

            logger.info(
                f"create_testbed(user: {user_id}, run_id: {run_id}, instance_id: {instance_id}, testbed_id: {testbed_id}) Job and Service created in namespace {self.namespace}."
            )

            return TestbedSummary(
                testbed_id=job.metadata.name,
                instance_id=job.metadata.labels.get("instance-id", "unknown"),
                status="Pending",
            )
        except ApiException as e:
            logger.exception(
                f"Error creating job or service for instance {instance_id} and user {user_id}"
            )
            raise RuntimeError("Error creating job or service")

    async def get_testbed(
        self,
        testbed_id: str,
        user_id: str = "default",
    ) -> Optional[TestbedDetailed]:
        await self.ensure_initialized()

        logger.info(f"get_testbed(testbed_id: {testbed_id}, user_id: {user_id})")
        job = await self._get_job(testbed_id)
        if (
            not job
            or not job.metadata
            or not job.metadata.labels
            or job.metadata.labels.get("user-id") != user_id
        ):
            return None

        status = await self._read_testbed_status_detailed(job.metadata.name)
        if not status:
            return None

        return TestbedDetailed(
            testbed_id=job.metadata.name,
            instance_id=job.metadata.labels.get("instance-id", "unknown"),
            status=status,
            external_ip=None,
        )

    def _extract_instance_id(self, testbed_id: str) -> str:
        return testbed_id.rsplit("-testbed-", 1)[0]

    async def create_client(
        self,
        testbed_id: str,
        user_id: str = "default",
        timeout: int = 30,
        run_id: Optional[str] = None,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> TestbedClient:
        await self.ensure_initialized()
        run_id = run_id or "default"

        logger.debug(
            f"create_client(testbed_id: {testbed_id}, user_id: {user_id}, timeout: {timeout}, run_id: {run_id})"
        )
        job = await self._get_job(testbed_id)
        # Only check service if not in cluster
        service = None if self.in_cluster else await self._get_service(testbed_id)

        if (
            not job
            or not job.metadata
            or not job.metadata.labels
            or job.metadata.labels.get("user-id") != user_id
        ):
            logger.warning(
                f"Testbed {testbed_id} not found or not owned by user {user_id}"
            )
            raise TestbedNotFoundError(f"Testbed {testbed_id} not found")

        # Also check service exists if not in cluster
        if not self.in_cluster and not service:
            logger.warning(f"Service for testbed {testbed_id} not found")
            raise TestbedNotFoundError(f"Service for testbed {testbed_id} not found")

        instance_id = job.metadata.labels.get("instance-id")

        if self.in_cluster:
            # Get pod IP directly when in cluster
            pod_list = await self.core_v1.list_namespaced_pod(
                namespace=self.namespace, label_selector=f"job-name={testbed_id}"
            )
            pod_list = cast(V1PodList, pod_list)
            if not pod_list or not pod_list.items:
                raise TestbedNotFoundError(f"Pod for testbed {testbed_id} not found")
            pod = pod_list.items[0]
            if not pod.status or not pod.status.pod_ip:
                raise TestbedNotFoundError(f"Pod IP for testbed {testbed_id} not found")
            pod_ip = pod.status.pod_ip
            base_url = f"http://{pod_ip}:8000"
        else:
            base_url = f"http://{await self._get_service_external_ip(testbed_id)}:8000"

        # Use the manager's session if no session provided
        session_to_use = session or self._session

        return TestbedClient(
            testbed_id=testbed_id,
            instance_id=instance_id,
            base_url=base_url,
            startup_timeout=timeout,
            ignored_tests=self.ignored_tests.get(instance_id, {}),
            in_cluster=self.in_cluster,
            namespace=self.namespace,
            session=session_to_use,
        )

    async def delete_testbed(self, testbed_id: str, user_id: str = "default"):
        await self.ensure_initialized()

        try:
            job = await self._get_job(testbed_id)
            if not job:
                logger.warning(f"Job {testbed_id} not found, skipping deletion")
                return
            if (
                not job.metadata
                or not job.metadata.labels
                or job.metadata.labels.get("user-id") != user_id
            ):
                logger.warning(
                    f"Job {testbed_id} not owned by user {user_id}, skipping deletion"
                )
                return

            # Only try to delete service if not in cluster
            if not self.in_cluster:
                try:
                    service = await self._get_service(testbed_id)
                    if service:
                        await self.core_v1.delete_namespaced_service(
                            name=testbed_id,
                            namespace=self.namespace,
                            body=client.V1DeleteOptions(
                                propagation_policy="Foreground", grace_period_seconds=0
                            ),
                        )
                        logger.info(f"Deleted service for {testbed_id}")
                    else:
                        logger.info(
                            f"Service for {testbed_id} not found, skipping deletion"
                        )
                except Exception as e:
                    logger.error(f"Failed to delete service {testbed_id}: {str(e)}")

            try:
                # Delete the job
                await self.batch_v1.delete_namespaced_job(
                    name=testbed_id,
                    namespace=self.namespace,
                    body=client.V1DeleteOptions(
                        propagation_policy="Foreground", grace_period_seconds=0
                    ),
                )
                logger.info(f"Deleted job for {testbed_id}")
            except Exception as e:
                logger.error(f"Failed to delete job {testbed_id}: {str(e)}")

        except ApiException as e:
            if e.status == 404:
                logger.warning(f"Job {testbed_id} not found.")
            else:
                error_message = f"Error deleting job {testbed_id}: {str(e)}"
                logger.exception(error_message)
                raise RuntimeError(error_message)
        except Exception as e:
            error_message = (
                f"Unexpected error during cleanup of job {testbed_id}: {str(e)}"
            )
            logger.exception(error_message)
            raise RuntimeError(error_message)

    async def delete_all_testbeds(self, user_id: str = "default"):
        if not self.batch_v1:
            await self.initialize()

        logger.info(f"Deleting all testbeds for user {user_id}")
        job_list = await self.batch_v1.list_namespaced_job(namespace=self.namespace)

        deleted_count = 0
        for job in job_list.items:
            if job.metadata.labels.get("user-id") != user_id:
                continue

            try:
                await self.delete_testbed(job.metadata.name, user_id)
                deleted_count += 1
            except Exception as e:
                logger.error(f"Failed to delete testbed {job.metadata.name}: {str(e)}")

        logger.info(f"Deleted {deleted_count} testbeds")
        return deleted_count

    async def _get_job(self, testbed_id: str) -> Optional[V1Job]:
        await self.ensure_initialized()
        try:
            response = await self.batch_v1.read_namespaced_job(
                name=testbed_id, namespace=self.namespace
            )
            return cast(V1Job, response)
        except ApiException as e:
            if e.status == 404:
                return None
            else:
                raise

    async def _read_testbed_status(self, job_name: str) -> str:
        if not self.batch_v1 or not self.core_v1:
            await self.initialize()

        # First check if job exists
        job = await self._get_job(job_name)
        if not job:
            logger.debug(f"Job {job_name} not found")
            return "NotFound"

        pod_list = await self.core_v1.list_namespaced_pod(
            namespace=self.namespace, label_selector=f"job-name={job_name}"
        )

        # If no pods found, we're still pending
        if not pod_list.items:
            logger.debug(f"Pod not found for job {job_name}. Pod list: {pod_list}")
            return "Pending"

        pod = pod_list.items[0]

        # If pod exists but no IP, we're still pending
        if not pod.status.pod_ip:
            return "Pending"

        pod_status = pod.status.phase

        # Only check service status if not in cluster
        if self.in_cluster:
            return pod_status
        else:
            service_status = await self._get_service_status(job_name)
            if pod_status == "Running" and service_status == "Running":
                return "Running"
            elif pod_status == "Pending" or service_status == "Pending":
                return "Pending"
            else:
                logger.warning(
                    f"Testbed {job_name} is Unknown: Pod status: {pod_status}, Service status: {service_status}"
                )
                return "Unknown"

    async def _read_testbed_status_detailed(
        self, job_name: str
    ) -> Optional[TestbedStatusDetailed]:
        await self.ensure_initialized()

        pod_list_response = await self.core_v1.list_namespaced_pod(
            namespace=self.namespace, label_selector=f"job-name={job_name}"
        )
        pod_list = cast(V1PodList, pod_list_response)

        if not pod_list or not pod_list.items:
            logger.warning(f"Pod not found for job {job_name}")
            return None

        pod = pod_list.items[0]
        testbed_status = ContainerStatus(
            ready=False,
            started=False,
            restart_count=0,
            state="unknown",
            reason=None,
            message=None,
        )
        sidecar_status = ContainerStatus(
            ready=False,
            started=False,
            restart_count=0,
            state="unknown",
            reason=None,
            message=None,
        )

        if pod.status and pod.status.container_statuses:
            for container in pod.status.container_statuses:
                container = cast(V1ContainerStatus, container)
                status = self._get_container_status(container)
                if container.name == "testbed":
                    testbed_status = status
                elif container.name == "sidecar":
                    sidecar_status = status

        return TestbedStatusDetailed(
            pod_phase=pod.status.phase if pod.status else "Unknown",
            testbed=testbed_status,
            sidecar=sidecar_status,
        )

    async def _get_service_status(self, testbed_id: str) -> str:
        await self.ensure_initialized()

        try:
            service = await self._get_service(testbed_id)
            if service and service.spec and service.spec.cluster_ip:
                return "Running"
            return "Pending"
        except ApiException as e:
            logger.warning(f"Service {testbed_id} not found: {str(e)}")
            if e.status == 404:
                return "NotFound"
            else:
                raise

    def _get_container_status(self, container: V1ContainerStatus) -> ContainerStatus:
        state = "unknown"
        reason = None
        message = None

        if container.state:
            if container.state.running:
                state = "running"
            elif container.state.waiting:
                state = "waiting"
                reason = (
                    container.state.waiting.reason if container.state.waiting else None
                )
                message = (
                    container.state.waiting.message if container.state.waiting else None
                )
            elif container.state.terminated:
                state = "terminated"
                reason = (
                    container.state.terminated.reason
                    if container.state.terminated
                    else None
                )
                message = (
                    container.state.terminated.message
                    if container.state.terminated
                    else None
                )

        return ContainerStatus(
            ready=container.ready if container.ready is not None else False,
            started=container.started if container.started is not None else False,
            restart_count=container.restart_count
            if container.restart_count is not None
            else 0,
            state=state,
            reason=reason,
            message=message,
        )

    async def _get_service_external_ip(self, testbed_id: str) -> str:
        await self.ensure_initialized()
        service = await self._get_service(testbed_id)
        if (
            service
            and service.status
            and service.status.load_balancer
            and service.status.load_balancer.ingress
        ):
            return service.status.load_balancer.ingress[0].ip
        raise ValueError(f"No external IP found for testbed {testbed_id}")

    def _create_service_manifest(
        self, testbed_id: str, user_id: str, run_id: str, instance_id: str
    ) -> str:
        context = {
            "testbed_id": testbed_id,
            "user_id": user_id,
            "run_id": run_id,
            "instance_id": instance_id,
            "namespace": self.namespace,
            "in_cluster": self.in_cluster,
        }
        manifest_yaml = self.service_template.render(context)
        return yaml.safe_load(manifest_yaml)

    async def cleanup_user_resources(self, user_id: str):
        await self.ensure_initialized()

        logger.info(f"Cleaning up all resources for user {user_id}")
        deleted_count = 0

        # Delete jobs
        job_list_response = await self.batch_v1.list_namespaced_job(
            namespace=self.namespace, label_selector=f"user-id={user_id}"
        )
        job_list = cast(V1JobList, job_list_response)

        if job_list and job_list.items:
            for job in job_list.items:
                try:
                    await self.batch_v1.delete_namespaced_job(
                        name=job.metadata.name if job.metadata else "",
                        namespace=self.namespace,
                        body=client.V1DeleteOptions(
                            propagation_policy="Foreground", grace_period_seconds=0
                        ),
                    )
                    deleted_count += 1
                except Exception as e:
                    logger.error(
                        f"Failed to delete job {job.metadata.name if job.metadata else '<unknown>'}: {str(e)}"
                    )

        # Delete services
        service_list_response = await self.core_v1.list_namespaced_service(
            namespace=self.namespace, label_selector=f"user-id={user_id}"
        )
        service_list = cast(V1ServiceList, service_list_response)

        if service_list and service_list.items:
            for service in service_list.items:
                try:
                    await self.core_v1.delete_namespaced_service(
                        name=service.metadata.name if service.metadata else "",
                        namespace=self.namespace,
                        body=client.V1DeleteOptions(
                            propagation_policy="Foreground", grace_period_seconds=0
                        ),
                    )
                    deleted_count += 1
                except Exception as e:
                    logger.error(
                        f"Failed to delete service {service.metadata.name if service.metadata else '<unknown>'}: {str(e)}"
                    )

        logger.info(f"Deleted {deleted_count} resources for user {user_id}")
        return deleted_count

    async def _get_service(self, testbed_id: str) -> Optional[V1Service]:
        await self.ensure_initialized()
        try:
            response = await self.core_v1.read_namespaced_service(
                name=testbed_id, namespace=self.namespace
            )
            return cast(V1Service, response)
        except ApiException as e:
            if e.status == 404:
                return None
            else:
                raise

    async def get_testbed_url(self, testbed_id: str) -> str:
        await self.ensure_initialized()
        service = await self._get_service(testbed_id)
        if service and service.spec and service.spec.cluster_ip:
            return f"http://{service.spec.cluster_ip}:8000"
        raise ValueError(f"Unable to find ClusterIP for testbed {testbed_id}")

    async def get_testbed_status(self, testbed_id: str, user_id: str) -> dict:
        status = await self._read_testbed_status(testbed_id)
        return {"status": status}

    def _generate_kubernetes_like_suffix(self, length=5):
        characters = string.ascii_lowercase + string.digits
        return "".join(random.choice(characters) for _ in range(length))

    def _generate_test_id(
        self, instance_id: str, user_id: str, run_id: str = "default"
    ) -> str:
        suffix = self._generate_kubernetes_like_suffix()
        instance_name = str(instance_id).replace("__", "-")
        user_prefix = str(user_id)[:4].lower()
        run_prefix = str(run_id)[:4].lower()
        return f"{instance_name}-{user_prefix}-{run_prefix}-{suffix}"

    def _config_map_name(self, instance_id: str) -> str:
        return f"instance-{instance_id.replace('_', '-')}-configmap"

    def _create_job_manifest(
        self, instance: SWEbenchInstance, user_id: str, testbed_id: str, run_id: str
    ) -> str:
        instance_id = instance.instance_id
        test_spec = TestSpec.from_instance(instance)

        # TODO: Set limits in test spec?
        if instance_id in high_cpu_instances or instance_id.startswith("sympy"):
            limit_cpu = "2.0"
            request_cpu = "1.0"
        else:
            limit_cpu = "1.0"
            request_cpu = "0.2"

        if instance_id.startswith("matplotlib"):
            limit_memory = "3.0Gi"
            request_memory = "2.0Gi"
        else:
            # limit_memory = "1Gi"
            # request_memory = "600Mi"
            limit_memory = "2.0Gi"
            request_memory = "1.0Gi"

        limit_cpu = "2.0"
        request_cpu = "1.0"

        if instance.dataset == "SWE-Gym/SWE-Gym":
            image_instance_id = instance_id.replace("__", "_s_")
            testbed_image = (
                f"{SWE_GYM_DOCKER_REGISTRY}/{SWE_GYM_IMAGE_PREFIX}{image_instance_id}"
            )
        else:
            if SWEBENCH_DOCKER_REGISTRY == "swebench":
                image_instance_id = instance_id.replace("__", "_1776_")
            else:
                image_instance_id = instance_id

            testbed_image = (
                f"{SWEBENCH_DOCKER_REGISTRY}/{SWEBENCH_IMAGE_PREFIX}{image_instance_id}"
            )

        # Ensure all values are strings
        context = {
            "job_name": str(testbed_id),
            "namespace": str(self.namespace),
            "instance_id": str(instance_id),
            "testbed_id": str(testbed_id),
            "user_id": str(user_id),
            "run_id": str(run_id),
            "testbed_image": testbed_image,
            "sidecar_image": "aorwall/moatless-testbed-sidecar:latest",
            "limit_cpu": str(limit_cpu),
            "limit_memory": str(limit_memory),
            "request_cpu": str(request_cpu),
            "request_memory": str(request_memory),
            "init_env_commands": test_spec.env_script_list,
        }
        manifest_yaml = self.job_template.render(context)
        return yaml.safe_load(manifest_yaml)
