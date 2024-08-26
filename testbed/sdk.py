import time
import logging
from contextlib import contextmanager
from testbed import utils
from testbed.client.manager import TestbedManager
from testbed.schema import EvaluationResult, Prediction, RunEvaluationRequest
from kubernetes import config

from testbed.swebench.test_spec import make_env_setup_script_list
from testbed.swebench.utils import load_swebench_dataset, load_swebench_instance

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
        logger.info(
            f"Creating testbed for instance {self.instance_id} in namespace {self.namespace}"
        )

        self.instance = load_swebench_instance(
            instance_id=self.instance_id, name="princeton-nlp/SWE-bench_Lite"
        )
        assert self.instance, f"Instance {self.instance_id} not found"

        response = self.manager.create_testbed(self.instance_id)
        self.testbed_id = response.testbed_id

        self.manager.wait_for_testbed_ready(self.testbed_id)
        self.client = self.manager.create_client(self.testbed_id)
        logger.info(f"Client created, running health check")

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.testbed_id:
            logger.info(f"Cleaning up - deleting testbed {self.testbed_id}")
            self.manager.delete_testbed(self.testbed_id)
        if self.manager:
            self.manager.close()

    def check_health(self, timeout=30):
        return self.client.check_health(timeout=timeout)

    def setup_swebench_environment(self):
        setup_commands = make_env_setup_script_list(self.instance)
        self.client.execute(setup_commands)

    def run_evaluation(self, run_id: str, patch: str | None = None) -> EvaluationResult:
        dataset = load_swebench_dataset(name="princeton-nlp/SWE-bench_Lite")
        instance = dataset[self.instance_id]

        request = RunEvaluationRequest(run_id=run_id, instance=instance, patch=patch)
        return self.client.run_evaluation(request)

    def save_file(self, file_path: str, content: str):
        """
        Save a file to the testbed.

        Args:
            file_path (str): The path where the file should be saved in the testbed.
            content (str): The content of the file to be saved.

        Returns:
            dict: The response from the save operation.

        Raises:
            Exception: If there's an error saving the file.
        """
        logger.info(f"Saving file to {file_path}")
        try:
            response = self.client.save_file(file_path, content)
            if "error" in response:
                raise Exception(f"Failed to save file: {response['error']}")
            logger.info(f"File saved successfully to {file_path}")
            return response
        except Exception as e:
            logger.error(f"Error saving file to {file_path}: {str(e)}")
            raise

    def read_file(self, file_path: str) -> str:
        """
        Read a file from the testbed.

        Args:
            file_path (str): The path of the file to be read from the testbed.

        Returns:
            str: The content of the file.

        Raises
            Exception: If there's an error reading the file.
        """
        logger.info(f"Reading file from {file_path}")
        try:
            content = self.client.get_file(file_path)
            if isinstance(content, dict) and "error" in content:
                raise Exception(f"Failed to read file: {content['error']}")
            logger.info(f"File read successfully from {file_path}")
            return content
        except Exception as e:
            logger.error(f"Error reading file from {file_path}: {str(e)}")
            raise
