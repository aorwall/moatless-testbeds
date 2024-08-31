import time
import logging
from contextlib import contextmanager
from testbed.client.manager import TestbedManager
from testbed.schema import EvaluationResult, Prediction, RunEvaluationRequest
from kubernetes import config

from testbed.swebench.utils import load_swebench_dataset, load_swebench_instance

logger = logging.getLogger(__name__)


class TestbedSDK:

    def __init__(self, instance_id: str, namespace: str, dataset_name: str = "princeton-nlp/SWE-bench_Lite", in_cluster: bool = False):
        self.instance_id = instance_id
        self.namespace = namespace
        self.manager = TestbedManager(namespace=self.namespace, in_cluster=in_cluster, dataset_name=dataset_name)
        self.testbed = None
        self.testbed_id = None
        self.dataset_name = dataset_name

    def __enter__(self):
        config.load_kube_config()
        self.manager = TestbedManager(namespace=self.namespace)
        logger.info(
            f"Creating testbed for instance {self.instance_id} in namespace {self.namespace}"
        )

        return self.manager.create_testbed(self.instance_id)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.testbed_id:
            logger.info(f"Cleaning up - deleting testbed {self.testbed_id}")
            self.manager.delete_testbed(self.testbed_id)
        if self.manager:
            self.manager.close()
