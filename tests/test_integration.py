import os
import pytest
import uuid
import logging
from testbed.sdk import TestbedSDK
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

load_dotenv()

logger.info(f"Loaded environment variables from .env file")
logger.info(f"KUBE_NAMESPACE: {os.getenv('KUBE_NAMESPACE')}")

@pytest.mark.integration
def test_full_integration():
    test_id = str(uuid.uuid4())[:8]
    logger.info(f"Starting integration test {test_id}")

    instance_id = "sympy__sympy-21847"

    with TestbedSDK(instance_id, os.getenv("KUBE_NAMESPACE")) as sdk:
        logger.info(f"Test {test_id}: Run health check on testbed")
        assert sdk.check_health(
            timeout=30
        ), f"Test {test_id}: Health check on testbed failed"
        logger.info(f"Test {test_id}: Health check successful")

        logger.info(f"Test {test_id}: Running evaluation")
        run_id = f"test_run_integration_{test_id}"
        patch = ""
        result = sdk.run_evaluation(run_id, patch)
        logger.info(f"Test {test_id}: Evaluation finished")

        assert result.resolved
        assert result.tests_status
        logger.info(f"Test {test_id}: Evaluation result received")

    logger.info(f"Test {test_id}: Integration test completed successfully")


if __name__ == "__main__":
    pytest.main(["-v", __file__])
