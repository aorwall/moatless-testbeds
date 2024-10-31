import json
import logging
import os
import random
import string
import time
from collections import namedtuple
from typing import Optional, List

import yaml
from jinja2 import Environment, FileSystemLoader
from kubernetes import client, config

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

KUBE_NAMESPACE = os.getenv("KUBE_NAMESPACE", "testbeds")

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
        try:
            config.load_incluster_config()
            self.in_cluster = True
            logger.info("Loaded in-cluster Kubernetes configuration.")
        except config.ConfigException:
            config.load_kube_config()
            self.in_cluster = False
            logger.info("Loaded local Kubernetes configuration.")

        self.namespace = namespace
        self.container_name = "testbed"

        self.core_v1 = client.CoreV1Api()
        self.batch_v1 = client.BatchV1Api()

        # Jinja2 environment setup
        self.template_dir = os.path.join(os.path.dirname(__file__), "template")
        self.template_file = "pod_template.yaml"
        self.env = Environment(loader=FileSystemLoader(self.template_dir))
        self.job_template = self.env.get_template("pod_template.yaml")
        self.service_template = self.env.get_template("service_template.yaml")
        self.ignored_tests = self.create_ignored_tests_dataset()

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

    def list_testbeds(self, user_id: str) -> List[TestbedSummary]:
        testbeds = []
        job_list = self.batch_v1.list_namespaced_job(namespace=self.namespace)
        for job in job_list.items:
            if job.metadata.labels.get("user-id") == user_id:
                status = self._read_testbed_status(job.metadata.name)
                testbeds.append(
                    TestbedSummary(
                        testbed_id=job.metadata.name,
                        instance_id=job.metadata.labels.get("instance-id", "unknown"),
                        status=status,
                    )
                )
        return testbeds

    def get_or_create_testbed(
        self,
        instance_id: str,
        user_id: str = "default",
        timeout: int = 60,
        run_id: str = "default",
    ) -> Optional[TestbedSummary]:
        logger.info(
            f"get_or_create_testbed(user: {user_id}, instance_id: {instance_id}, run_id: {run_id})"
        )
        
        # Wait for any deletion to complete before creating a new job
        start_time = time.time()
        while True:
            if time.time() - start_time > timeout:
                raise TimeoutError("Timed out waiting for previous job deletion to complete")
                
            try:
                job_list = self.batch_v1.list_namespaced_job(namespace=self.namespace)
                found_job = None
                for job in job_list.items:
                    if (
                        job.metadata.labels.get("instance-id") == instance_id
                        and job.metadata.labels.get("user-id") == user_id
                        and job.metadata.labels.get("run-id") == run_id
                    ):
                        # Check if job is being deleted
                        if job.metadata.deletion_timestamp:
                            time.sleep(0.5)
                            continue
                        found_job = job
                        break
                
                if found_job:
                    status = self._read_testbed_status(found_job.metadata.name)
                    logger.info(
                        f"Found existing testbed job {found_job.metadata.name} with status {status}"
                    )
                    return TestbedSummary(
                        testbed_id=found_job.metadata.name,
                        instance_id=found_job.metadata.labels.get("instance-id", "unknown"),
                        status=status,
                        run_id=run_id,
                    )
                break  # No existing job found, exit loop
                
            except client.exceptions.ApiException as e:
                logger.warning(f"API error while checking jobs: {e}")
                time.sleep(0.5)
        
        return self.create_testbed(instance_id, user_id, timeout, run_id)

    def create_testbed(
        self,
        instance_id: str,
        user_id: str = "default",
        timeout: int = 60,
        run_id: str = "default",
    ) -> TestbedSummary:
        logger.info(
            f"create_testbed(user: {user_id}, instance_id: {instance_id}, run_id: {run_id}) creating new testbed."
        )
        start_time = time.time()
        try:
            instance = load_swebench_instance(instance_id)
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
            self.batch_v1.create_namespaced_job(
                body=job_manifest, namespace=self.namespace
            )
            logger.info(f"Created job for {testbed_id}")

            # Only create service if not in cluster
            if not self.in_cluster:
                service_manifest = self._create_service_manifest(
                    testbed_id, user_id, run_id, instance_id
                )
                self.core_v1.create_namespaced_service(
                    body=service_manifest, namespace=self.namespace
                )
                logger.info(f"Created service for {testbed_id}")

            # Wait for job and optionally service to be created
            while True:
                if time.time() - start_time > timeout:
                    raise TimeoutError(f"Creation of job or service {testbed_id} timed out")

                job = self._get_job(testbed_id)
                if not self.in_cluster:
                    service = self._get_service(testbed_id)
                    if job and service:
                        break
                else:
                    if job:
                        break

                time.sleep(0.1)

            logger.info(
                f"create_testbed(user: {user_id}, instance_id: {instance_id}, testbed_id: {testbed_id}) Job and Service created in namespace {self.namespace}."
            )

            return TestbedSummary(
                testbed_id=job.metadata.name,
                instance_id=job.metadata.labels.get("instance-id", "unknown"),
                status="Pending",
                run_id=run_id,
            )
        except client.exceptions.ApiException as e:
            logger.exception(
                f"Error creating job or service for instance {instance_id} and user {user_id}"
            )
            raise RuntimeError("Error creating job or service")

    def get_testbed(
        self,
        testbed_id: str,
        user_id: str = "default",
    ) -> Optional[TestbedDetailed]:
        logger.info(f"get_testbed(testbed_id: {testbed_id}, user_id: {user_id})")
        job = self._get_job(testbed_id)
        if not job or job.metadata.labels.get("user-id") != user_id:
            return None

        return self._read_testbed_status_detailed(job.metadata.name)

    def _extract_instance_id(self, testbed_id: str) -> str:
        return testbed_id.rsplit("-testbed-", 1)[0]

    def create_client(
        self,
        testbed_id: str,
        user_id: str = "default",
        timeout: int = 30,
        run_id: str = "default",
    ) -> TestbedClient:
        logger.debug(
            f"create_client(testbed_id: {testbed_id}, user_id: {user_id}, timeout: {timeout}, run_id: {run_id})"
        )
        job = self._get_job(testbed_id)
        # Only check service if not in cluster
        service = None if self.in_cluster else self._get_service(testbed_id)

        if not job or job.metadata.labels.get("user-id") != user_id or job.metadata.labels.get("run-id") != run_id:
            logger.warning(
                f"Testbed {testbed_id} not found or not owned by user {user_id} with run_id {run_id}"
            )
            raise TestbedNotFoundError(f"Testbed {testbed_id} not found")

        # Also check service exists if not in cluster
        if not self.in_cluster and not service:
            logger.warning(f"Service for testbed {testbed_id} not found")
            raise TestbedNotFoundError(f"Service for testbed {testbed_id} not found")

        instance_id = job.metadata.labels.get("instance-id")

        if self.in_cluster:
            # Get pod IP directly when in cluster
            pod_list = self.core_v1.list_namespaced_pod(
                namespace=self.namespace, 
                label_selector=f"job-name={testbed_id}"
            )
            if not pod_list.items:
                raise TestbedNotFoundError(f"Pod for testbed {testbed_id} not found")
            pod_ip = pod_list.items[0].status.pod_ip
            base_url = f"http://{pod_ip}:8000"
        else:
            base_url = f"http://{self._get_service_external_ip(testbed_id)}:8000"

        return TestbedClient(
            testbed_id=testbed_id,
            instance_id=instance_id,
            base_url=base_url,
            startup_timeout=timeout,
            ignored_tests=self.ignored_tests.get(instance_id, {}),
            in_cluster=self.in_cluster,
            namespace=self.namespace,
        )

    def delete_testbed(self, testbed_id: str, user_id: str = "default"):
        try:
            job = self._get_job(testbed_id)
            if not job:
                logger.warning(f"Job {testbed_id} not found, skipping deletion")
                return
            if job.metadata.labels.get("user-id") != user_id:
                logger.warning(
                    f"Job {testbed_id} not owned by user {user_id}, skipping deletion"
                )
                return

            # Only try to delete service if not in cluster
            if not self.in_cluster:
                try:
                    service = self._get_service(testbed_id)
                    if service:
                        self.core_v1.delete_namespaced_service(
                            name=testbed_id,
                            namespace=self.namespace,
                            body=client.V1DeleteOptions(
                                propagation_policy="Foreground", grace_period_seconds=0
                            ),
                        )
                        logger.info(f"Deleted service for {testbed_id}")
                    else:
                        logger.info(f"Service for {testbed_id} not found, skipping deletion")
                except Exception as e:
                    logger.error(f"Failed to delete service {testbed_id}: {str(e)}")

            try:
                # Delete the job
                response = self.batch_v1.delete_namespaced_job(
                    name=testbed_id,
                    namespace=self.namespace,
                    body=client.V1DeleteOptions(
                        propagation_policy="Foreground", grace_period_seconds=0
                    ),
                )

                logger.info(f"Deleted job for {testbed_id}")
            except Exception as e:
                logger.error(f"Failed to delete job {testbed_id}: {str(e)}")

        except client.exceptions.ApiException as e:
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

    def delete_all_testbeds(
        self, user_id: str = "default"
    ):
        logger.info(f"Deleting all testbeds for user {user_id}")
        job_list = self.batch_v1.list_namespaced_job(namespace=self.namespace)

        deleted_count = 0
        for job in job_list.items:
            if job.metadata.labels.get("user-id") != user_id:
                continue

            try:
                self.delete_testbed(job.metadata.name, user_id)
                deleted_count += 1
            except Exception as e:
                logger.error(f"Failed to delete testbed {job.metadata.name}: {str(e)}")

        logger.info(f"Deleted {deleted_count} testbeds")
        return deleted_count

    def close(self):
        self.core_v1.api_client.close()
        self.batch_v1.api_client.close()

    def _generate_kubernetes_like_suffix(self, length=5):
        characters = string.ascii_lowercase + string.digits
        return "".join(random.choice(characters) for _ in range(length))

    def _generate_test_id(
        self, instance_id: str, user_id: str, run_id: str = "default"
    ) -> str:
        suffix = self._generate_kubernetes_like_suffix()
        instance_name = instance_id.replace("__", "-")
        user_prefix = user_id[:4].lower()
        run_prefix = run_id[:4].lower()
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
            limit_memory = "1.2Gi"
            request_memory = "800Mi"
        else:
            limit_memory = "1Gi"
            request_memory = "600Mi"

        context = {
            "job_name": testbed_id,
            "namespace": self.namespace,
            "instance_id": instance_id,
            "testbed_id": testbed_id,
            "user_id": user_id,
            "run_id": run_id,
            "testbed_image": f"moatless.azurecr.io/sweb.eval.x86_64.{instance_id}",
            "sidecar_image": "aorwall/moatless-testbed-sidecar:latest",
            "limit_cpu": limit_cpu,
            "limit_memory": limit_memory,
            "request_cpu": request_cpu,
            "request_memory": request_memory,
            "init_env_commands": test_spec.env_script_list,
        }
        manifest_yaml = self.job_template.render(context)
        return yaml.safe_load(manifest_yaml)

    def _get_job(self, testbed_id: str):
        try:
            return self.batch_v1.read_namespaced_job(
                name=testbed_id, namespace=self.namespace
            )
        except client.exceptions.ApiException as e:
            if e.status == 404:
                return None
            else:
                raise

    def _read_testbed_status(
        self, job_name: str
    ) -> str:
        # First check if job exists
        job = self._get_job(job_name)
        if not job:
            logger.debug(f"Job {job_name} not found")
            return "NotFound"
        
        pod_list = self.core_v1.list_namespaced_pod(
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
            service_status = self._get_service_status(job_name)
            if pod_status == "Running" and service_status == "Running":
                return "Running"
            elif pod_status == "Pending" or service_status == "Pending":
                return "Pending"
            else:
                logger.warning(
                    f"Testbed {job_name} is Unknown: Pod status: {pod_status}, Service status: {service_status}"
                )
                return "Unknown"

    def _read_testbed_status_detailed(
        self, job_name: str
    ) -> Optional[TestbedStatusDetailed]:
        pod_list = self.core_v1.list_namespaced_pod(
            namespace=self.namespace, label_selector=f"job-name={job_name}"
        )
        if pod_list.items:
            pod = pod_list.items[0]
            testbed_status = ContainerStatus(
                ready=False, started=False, restart_count=0, state="unknown"
            )
            sidecar_status = ContainerStatus(
                ready=False, started=False, restart_count=0, state="unknown"
            )

            if pod.status and pod.status.container_statuses:
                for container in pod.status.container_statuses:
                    status = self._get_container_status(container)
                    if container.name == "testbed":
                        testbed_status = status
                    elif container.name == "sidecar":
                        sidecar_status = status

            # Only get service status if not in cluster
            service_status = "NotRequired" if self.in_cluster else self._get_service_status(job_name)

            return TestbedStatusDetailed(
                pod_phase=pod.status.phase if pod.status else "Unknown",
                testbed=testbed_status,
                sidecar=sidecar_status,
                service=service_status,
            )
        else:
            logger.warning(f"Pod not found for job {job_name}")
            return None

    def _get_service_status(self, testbed_id: str) -> str:
        try:
            service = self.core_v1.read_namespaced_service(
                name=testbed_id, namespace=self.namespace
            )
            if service.spec.cluster_ip:
                return "Running"
            return "Pending"
        except client.exceptions.ApiException as e:
            logger.warning(f"Service {testbed_id} not found: {str(e)}")
            if e.status == 404:
                return "NotFound"
            else:
                raise

    def _get_container_status(self, container) -> ContainerStatus:
        state = "pending"
        reason = None
        message = None

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

        return ContainerStatus(
            ready=container.ready,
            started=container.started,
            restart_count=container.restart_count,
            state=state,
            reason=reason,
            message=message,
        )

    def _get_service_external_ip(self, testbed_id: str) -> str:
        service = self.core_v1.read_namespaced_service(
            name=testbed_id, namespace=self.namespace
        )
        if service.status.load_balancer.ingress:
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

    def cleanup_user_resources(self, user_id: str):
        logger.info(f"Cleaning up all resources for user {user_id}")
        deleted_count = 0

        # Delete jobs
        job_list = self.batch_v1.list_namespaced_job(
            namespace=self.namespace, label_selector=f"user-id={user_id}"
        )
        for job in job_list.items:
            try:
                self.batch_v1.delete_namespaced_job(
                    name=job.metadata.name,
                    namespace=self.namespace,
                    body=client.V1DeleteOptions(
                        propagation_policy="Foreground", grace_period_seconds=0
                    ),
                )
                deleted_count += 1
            except Exception as e:
                logger.error(f"Failed to delete job {job.metadata.name}: {str(e)}")

        # Delete services
        service_list = self.core_v1.list_namespaced_service(
            namespace=self.namespace, label_selector=f"user-id={user_id}"
        )
        for service in service_list.items:
            try:
                self.core_v1.delete_namespaced_service(
                    name=service.metadata.name,
                    namespace=self.namespace,
                    body=client.V1DeleteOptions(
                        propagation_policy="Foreground", grace_period_seconds=0
                    ),
                )
                deleted_count += 1
            except Exception as e:
                logger.error(
                    f"Failed to delete service {service.metadata.name}: {str(e)}"
                )

        logger.info(f"Deleted {deleted_count} resources for user {user_id}")
        return deleted_count

    def _get_service(self, testbed_id: str):
        try:
            return self.core_v1.read_namespaced_service(
                name=testbed_id, namespace=self.namespace
            )
        except client.exceptions.ApiException as e:
            if e.status == 404:
                return None
            else:
                raise

    def get_testbed_url(self, testbed_id: str) -> str:
        service = self.core_v1.read_namespaced_service(
            name=testbed_id, namespace=self.namespace
        )
        if service.spec.cluster_ip:
            return f"http://{service.spec.cluster_ip}:8000"
        raise ValueError(f"Unable to find ClusterIP for testbed {testbed_id}")

    def get_testbed_status(
        self, testbed_id: str, user_id: str
    ) -> dict:
        status = self._read_testbed_status(testbed_id)
        return {"status": status}
