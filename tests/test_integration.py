import os
import pytest
import uuid
import logging

from testbed.client.manager import TestbedManager
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
def test_kubernetes():
    test_id = str(uuid.uuid4())[:8]
    logger.info(f"Starting integration test {test_id}")
    instance_id = "django__django-15731"

    manager = TestbedManager(namespace="testbed-dev", in_cluster=False)
    try:
        testbed = manager.get_or_create_testbed(instance_id=instance_id, user_id="testing", timeout=30)
        testbed_client = manager.create_client(testbed_id=testbed.testbed_id, instance_id=instance_id, user_id="testing")
        testbed_client.wait_until_ready(timeout=600)
        logger.info(f"Test {test_id}: Running evaluation")
        run_id = f"test_run_integration_{test_id}"

        response = testbed_client.execute(["echo 'Hello, World!'"])
        assert response.output == "Hello, World!\n"

        test_result = testbed_client.run_tests()
        print(test_result.model_dump_json(indent=2))

        eval_result = testbed_client.run_evaluation()
        print(eval_result.model_dump_json(indent=2))
        assert eval_result.resolved
    except Exception as e:
        logger.exception(f"Error during integration test {test_id}: {e}")
        raise
    finally:
        manager.delete_testbed(testbed_id=testbed.testbed_id, user_id="testing")

def test_http():
    test_id = str(uuid.uuid4())[:8]
    logger.info(f"Starting integration test {test_id}")

    instance_id = "django__django-15731"

    sdk = TestbedSDK(base_url="http://74.241.176.229", api_key=os.getenv("TESTBED_API_KEY"))
    testbed = sdk.get_or_create_testbed(instance_id=instance_id)

    try:
        status = sdk.get_testbed(testbed_id=testbed.testbed_id)
        print(status.model_dump_json(indent=2))

        test_result = sdk.run_tests(testbed_id=testbed.testbed_id)
        print(test_result.model_dump_json(indent=2))

        eval_result = sdk.run_evaluation(testbed_id=testbed.testbed_id)
        print(eval_result.model_dump_json(indent=2))
        assert eval_result.resolved
    except Exception as e:
        logger.exception(f"Error during integration test {test_id}: {e}")
        raise
    finally:
        sdk.delete_testbed(testbed_id=testbed.testbed_id)


if __name__ == "__main__":
    pytest.main(["-v", __file__])
