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


def run_evaluation(instance_id: str):    
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

        logger.info("Running evaluation script...")
        result = testbed.run_evaluation()

        logger.info("Cleaning up testbed instance...")
        sdk.delete_testbed(testbed_id=testbed.testbed_id)

        if result.resolved:
            logger.info("✅ Evaluation completed successfully!")
        else:
            logger.info(f"Evaluation output:\n{result.model_dump_json(indent=2)}")
            logger.error("❌ Evaluation failed")

        return result.resolved

    except Exception as e:
        logger.exception(f"❌ Evaluation failed")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Run evaluation on Testbed API')
    parser.add_argument('--instance-id', type=str,
                        help='Instance ID to use for evaluation (e.g., django__django-11133)')
    
    args = parser.parse_args()
    success = run_evaluation(args.instance_id)
    sys.exit(0 if success else 1) 
