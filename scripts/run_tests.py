#!/usr/bin/env python3

import json
import os
import logging
import sys
import argparse

from dotenv import load_dotenv
from testbeds.sdk import TestbedSDK

logging.basicConfig(
    level=logging.INFO, 
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def run_tests(instance_id: str, test_files: list[str] = None):
    if not os.getenv("TESTBED_HOSTNAME"):
        logger.error("TESTBED_HOSTNAME is not set")
        return False
    
    if not os.getenv("TESTBED_API_KEY"):
        logger.error("TESTBED_API_KEY is not set")
        return False

    hostname = os.getenv("TESTBED_HOSTNAME")
    api_key = os.getenv("TESTBED_API_KEY")

    logger.info(f"Starting evaluation for instance: {instance_id}")

    try:
        sdk = TestbedSDK(
            base_url=hostname,
            api_key=api_key
        )

        logger.info("Creating testbed instance...")
        testbed = sdk.create_client(instance_id=instance_id)
        logger.info(f"Created Testbed ID: {testbed.testbed_id}")

        logger.info(f"Waiting for testbed to be ready...")
        testbed.wait_until_ready()

        # Use provided test files or fall back to test_patch files
        if test_files is None:
            test_files = testbed.test_spec.get_test_patch_files()

        logger.info("Running tests...")
        result = testbed.run_tests(test_files)

        logger.info("Cleaning up testbed instance...")
        sdk.delete_testbed(testbed_id=testbed.testbed_id)

        logger.info(f"Test results:\n{result.model_dump_json(indent=2)}")

        return True

    except Exception as e:
        logger.exception(f"❌ Failed to run tests")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Run tests on Testbed API')
    parser.add_argument('--instance-id', type=str,
                        help='Instance ID to test (e.g., django__django-11133)')
    parser.add_argument('--test-files', nargs='+',
                        help='List of test files to run (optional)')
    
    args = parser.parse_args()
    success = run_tests(args.instance_id, args.test_files)
    sys.exit(0 if success else 1) 