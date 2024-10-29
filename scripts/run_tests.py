#!/usr/bin/env python3

import json
import os
import logging
import sys
import argparse

from dotenv import load_dotenv
from testbed.sdk import TestbedSDK

logging.basicConfig(
    level=logging.INFO, 
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def run_evaluation(instance_id="django__django-11133"):
    namespace = os.getenv("NAMESPACE")
    ip = os.getenv("TESTBED_API_IP")
    api_key = os.getenv("TESTBED_API_KEY")

    if not all([namespace, ip, api_key]):
        logger.error("Missing required environment variables")
        return False

    logger.info(f"Starting evaluation for instance: {instance_id}")

    try:
        sdk = TestbedSDK(
            base_url=f"http://{ip}",
            api_key=api_key
        )

        logger.info("Creating evaluation instance...")
        testbed = sdk.create_client(instance_id=instance_id)
        logger.info(f"Created Testbed ID: {testbed.testbed_id}")

        logger.info(f"Waiting for testbed to be ready...")
        testbed.wait_until_ready()

        test_files = testbed.test_spec.get_test_patch_files()

        logger.info("Running tests...")
        result = testbed.run_tests(test_files)

        logger.info("Cleaning up evaluation instance...")
        sdk.delete_testbed(testbed_id=testbed.testbed_id)

        logger.info(f"Test results:\n{result.model_dump_json(indent=2)}")

        return True

    except Exception as e:
        logger.exception(f"❌ Failed to run tests")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Run evaluation on Testbed API')
    parser.add_argument('--instance-id', type=str,
                        help='Instance ID to use for evaluation (default: django__django-11133)')
    
    args = parser.parse_args()
    success = run_evaluation(args.instance_id)
    sys.exit(0 if success else 1) 
