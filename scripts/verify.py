#!/usr/bin/env python3

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


def verify_installation(instance_id="django__django-11133"):
    namespace = os.getenv("NAMESPACE")
    ip = os.getenv("TESTBED_API_IP")
    api_key = os.getenv("TESTBED_API_KEY")

    if not all([namespace, ip, api_key]):
        logger.error("Missing required environment variables")
        return False

    logger.info(f"Testing with instance: {instance_id}")

    try:
        sdk = TestbedSDK(
            base_url=f"http://{ip}",
            api_key=api_key
        )

        logger.info("Creating test instance...")
        testbed = sdk.create_client(instance_id=instance_id)
        testbed.wait_until_ready()

        logger.info("Running basic command test...")
        result = testbed.execute("echo Installation verification successful")

        logger.info("Cleaning up test instance...")
        sdk.delete_testbed(testbed_id=testbed.testbed_id)

        logger.info("✅ Verification completed successfully!")
        return True

    except Exception as e:
        logger.exception(f"❌ Verification failed")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Verify Testbed API installation')
    parser.add_argument('--instance-id', type=str, default="django__django-15731",
                      help='Instance ID to use for verification (default: django__django-15731)')
    
    args = parser.parse_args()
    success = verify_installation(args.instance_id)
    sys.exit(0 if success else 1)
