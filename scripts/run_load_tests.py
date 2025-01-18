#!/usr/bin/env python3

import json
import os
import logging
import sys
import argparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple

from testbeds.sdk import TestbedSDK

logging.basicConfig(
    level=logging.INFO, 
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

INSTANCES = [
    "django__django-11133",
    "matplotlib__matplotlib-22835",
    "pydata__xarray-4094",
    "sympy__sympy-14308",
    "psf__requests-1963"
]

def run_single_test(instance_id: str) -> Tuple[str, bool, float]:
    start_time = time.time()
    
    if not all([os.getenv("TESTBED_HOSTNAME"), os.getenv("TESTBED_API_KEY")]):
        logger.error("Missing required environment variables")
        return instance_id, False, 0.0

    hostname = os.getenv("TESTBED_HOSTNAME")
    api_key = os.getenv("TESTBED_API_KEY")

    try:
        sdk = TestbedSDK(
            base_url=hostname,
            api_key=api_key
        )

        logger.info(f"Starting test for instance: {instance_id}")
        with sdk.create_client(instance_id=instance_id) as testbed:
            logger.info(f"Created Testbed ID: {testbed.testbed_id}")
            test_files = testbed.test_spec.get_test_patch_files()
            result = testbed.run_tests(test_files)
            
            elapsed_time = time.time() - start_time
            logger.info(f"Instance {instance_id} completed in {elapsed_time:.2f}s")
            return instance_id, True, elapsed_time

    except Exception as e:
        elapsed_time = time.time() - start_time
        logger.exception(f"❌ Failed to run tests for {instance_id}")
        return instance_id, False, elapsed_time

def run_instance_iterations(instance_id: str, iterations: int) -> List[Tuple[str, bool, float]]:
    results = []
    logger.info(f"Starting {iterations} iterations for instance: {instance_id}")
    
    for i in range(iterations):
        result = run_single_test(instance_id)
        results.append(result)
        logger.info(f"Completed iteration {i+1}/{iterations} for {instance_id}")
    
    return results

def run_load_test(iterations: int = 10, max_workers: int = 5) -> dict:
    results = {
        "iterations": iterations,
        "max_workers": max_workers,
        "tests": [],
        "summary": {
            "total_time": 0,
            "success_rate": 0,
            "avg_time_per_instance": {}
        }
    }
    
    start_time = time.time()
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_instance = {
            executor.submit(run_instance_iterations, instance, iterations): instance 
            for instance in INSTANCES
        }
        
        for future in as_completed(future_to_instance):
            instance_results = future.result()
            for instance_id, success, elapsed_time in instance_results:
                results["tests"].append({
                    "instance_id": instance_id,
                    "success": success,
                    "time": elapsed_time
                })
    
    # Calculate summary statistics
    total_time = time.time() - start_time
    success_count = sum(1 for test in results["tests"] if test["success"])
    total_tests = len(INSTANCES) * iterations
    
    # Calculate average time per instance
    instance_times = {instance: [] for instance in INSTANCES}
    for test in results["tests"]:
        instance_times[test["instance_id"]].append(test["time"])
    
    avg_times = {
        instance: sum(times) / len(times) 
        for instance, times in instance_times.items()
    }
    
    results["summary"].update({
        "total_time": total_time,
        "success_rate": (success_count / total_tests) * 100,
        "avg_time_per_instance": avg_times
    })
    
    return results

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Run load tests on Testbed API')
    parser.add_argument('--iterations', type=int, default=10,
                       help='Number of test iterations to run')
    parser.add_argument('--max-workers', type=int, default=5,
                       help='Maximum number of parallel workers')
    parser.add_argument('--output', type=str,
                       help='Output file for results (JSON)')
    
    args = parser.parse_args()
    
    results = run_load_test(args.iterations, args.max_workers)
    
    # Print summary
    logger.info("\nLoad Test Summary:")
    logger.info(f"Total time: {results['summary']['total_time']:.2f}s")
    logger.info(f"Success rate: {results['summary']['success_rate']:.1f}%")
    logger.info("\nAverage time per instance:")
    for instance, avg_time in results['summary']['avg_time_per_instance'].items():
        logger.info(f"{instance}: {avg_time:.2f}s")
    
    # Save results if output file specified
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(results, f, indent=2)
        logger.info(f"\nDetailed results saved to {args.output}")
    
    success = results['summary']['success_rate'] == 100
    sys.exit(0 if success else 1) 