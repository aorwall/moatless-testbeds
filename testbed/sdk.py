import time
import logging
from contextlib import contextmanager
from testbed import utils
from testbed.client.manager import TestbedManager
from testbed.schema import EvaluationResult, Prediction
from kubernetes import config

logger = logging.getLogger(__name__)


class TestbedSDK:
    def __init__(self, instance_id: str, namespace: str):
        self.instance_id = instance_id
        self.namespace = namespace
        self.manager = None
        self.client = None
        self.testbed_id = None

    def __enter__(self):
        config.load_kube_config()
        self.manager = TestbedManager(namespace=self.namespace)
        logger.info(f"Creating testbed for instance {self.instance_id} in namespace {self.namespace}")
        response = self.manager.create_testbed(self.instance_id)
        self.testbed_id = response.testbed_id
        utils.wait_for_testbed_ready(self.manager, self.testbed_id)
        self.client = self.manager.create_client(self.testbed_id)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.testbed_id:
            logger.info(f"Cleaning up - deleting testbed {self.testbed_id}")
            self.manager.delete_testbed(self.testbed_id)
        if self.manager:
            self.manager.close()

    def _wait_for_testbed_ready(self, max_wait_time=300):
        start_time = time.time()
        logger.info(f"Waiting for testbed to be ready (max {max_wait_time} seconds)")
        while time.time() - start_time < max_wait_time:
            testbed = self.manager.get_testbed(self.testbed_id)
            if (
                testbed
                and testbed.status.pod_phase == "Running"
                and testbed.status.testbed.ready
                and testbed.status.testbed.state == "running"
                and testbed.status.sidecar.ready
                and testbed.status.sidecar.state == "running"
                and testbed.external_ip
            ):
                logger.info("Testbed is ready")
                return
            time.sleep(5)
            logger.info("Still waiting for testbed to be ready...")
        raise TimeoutError(
            f"Testbed {self.testbed_id} did not become ready within {max_wait_time} seconds"
        )

    def check_health(self, timeout=30):
        return self.client.check_health(timeout=timeout)

    def run_evaluation(self, run_id, patch) -> EvaluationResult:
        prediction = Prediction(
            run_id=run_id, instance_id=self.instance_id, patch=patch
        )
        return self.client.run_evaluation(prediction)
