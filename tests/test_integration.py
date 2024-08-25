import os
import pytest
import uuid
import logging
from testbed.sdk import TestbedSDK
from dotenv import load_dotenv
from kubernetes import client, config

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

load_dotenv()

def is_correct_k8s_context():
    try:
        config.load_kube_config()
        _, active_context = config.list_kube_config_contexts()
        current_namespace = active_context['context']['namespace']
        correct_namespace = current_namespace == os.getenv('KUBE_NAMESPACE')
        if not correct_namespace:
            logger.error(f"Current namespace is {current_namespace}, expected {os.getenv('KUBE_NAMESPACE')}")
        return correct_namespace
    except Exception as e:
        logger.error(f"Error checking Kubernetes context: {e}")
        return False

@pytest.mark.skipif(not is_correct_k8s_context(), reason="Incorrect Kubernetes context or namespace")
def test_full_integration():
    test_id = str(uuid.uuid4())[:8]
    logger.info(f"Starting integration test {test_id}")

    instance_id = "sympy__sympy-21847"

    with TestbedSDK(instance_id, os.getenv("KUBE_NAMESPACE")) as sdk:
        logger.info(f"Test {test_id}: Running evaluation")
        run_id = f"test_run_integration_{test_id}"

        content = sdk.read_file("sympy/polys/monomials.py")
        logger.info(f"Test {test_id}: File content: {content}")
        patch = ""
        result = sdk.run_evaluation(run_id, patch)
        logger.info(f"Test {test_id}: Evaluation finished")

        assert result.resolved
        assert result.tests_status
        logger.info(f"Test {test_id}: Evaluation result received")

        content = sdk.read_file("sympy/polys/monomials.py")
        logger.info(f"Test {test_id}: File content: {content}")

    logger.info(f"Test {test_id}: Integration test completed successfully")


if __name__ == "__main__":
    pytest.main(["-v", __file__])