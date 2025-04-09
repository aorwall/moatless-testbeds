#!/usr/bin/env python3

import json
import os
import logging
import sys
import argparse
import asyncio
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

from dotenv import load_dotenv
from testbeds.sdk import TestbedSDK
from testbeds.schema import SWEbenchInstance

load_dotenv()

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class LatencyStats:
    def __init__(self):
        self.start_time = time.time()
        self.instance_times: Dict[str, Dict[str, float]] = {}

    def start_instance(self, instance_id: str):
        if instance_id not in self.instance_times:
            self.instance_times[instance_id] = {
                "start": time.time(),
                "setup": 0,
                "evaluation": 0,
                "total": 0,
            }

    def record_setup(self, instance_id: str):
        if instance_id in self.instance_times:
            setup_time = time.time() - self.instance_times[instance_id]["start"]
            self.instance_times[instance_id]["setup"] = setup_time

    def record_evaluation(self, instance_id: str):
        if instance_id in self.instance_times:
            eval_time = time.time() - (
                self.instance_times[instance_id]["start"]
                + self.instance_times[instance_id]["setup"]
            )
            self.instance_times[instance_id]["evaluation"] = eval_time
            self.instance_times[instance_id]["total"] = (
                time.time() - self.instance_times[instance_id]["start"]
            )

    def get_summary(self) -> Dict[str, Any]:
        total_time = time.time() - self.start_time
        completed_instances = len(
            [i for i in self.instance_times.values() if i["total"] > 0]
        )

        if completed_instances == 0:
            return {
                "total_time": total_time,
                "instances_completed": 0,
                "avg_setup_time": 0,
                "avg_eval_time": 0,
                "avg_total_time": 0,
            }

        avg_setup = (
            sum(i["setup"] for i in self.instance_times.values()) / completed_instances
        )
        avg_eval = (
            sum(i["evaluation"] for i in self.instance_times.values())
            / completed_instances
        )
        avg_total = (
            sum(i["total"] for i in self.instance_times.values()) / completed_instances
        )

        return {
            "total_time": total_time,
            "instances_completed": completed_instances,
            "avg_setup_time": avg_setup,
            "avg_eval_time": avg_eval,
            "avg_total_time": avg_total,
            "instance_details": self.instance_times,
        }


async def evaluate_instance(
    sdk: TestbedSDK, instance: SWEbenchInstance, stats: LatencyStats, log_dir: str
) -> bool:
    instance_id = instance.instance_id
    stats.start_instance(instance_id)

    try:
        logger.info(f"Starting evaluation for instance: {instance_id}")
        async with await sdk.create_async_client(
            instance=instance, instance_id=instance_id, log_dir=log_dir
        ) as testbed:
            stats.record_setup(instance_id)
            logger.info(f"Running evaluation for instance {instance_id}")

            result = await testbed.run_evaluation()
            stats.record_evaluation(instance_id)

            if result.resolved:
                logger.info(f"✅ Instance {instance_id} completed successfully!")
            else:
                logger.error(f"❌ Instance {instance_id} failed")
                logger.info(f"Evaluation output:\n{result.model_dump_json(indent=2)}")

            return bool(result.resolved or False)

    except Exception as e:
        logger.exception(f"❌ Evaluation failed for instance {instance_id}")
        return False


async def run_parallel_evaluation(
    instances: List[SWEbenchInstance], parallel_limit: int = 16, log_dir: str = "logs"
) -> bool:
    if not os.getenv("TESTBED_HOSTNAME"):
        logger.error("TESTBED_HOSTNAME is not set")
        return False

    if not os.getenv("TESTBED_API_KEY"):
        logger.error("TESTBED_API_KEY is not set")
        return False

    hostname = os.getenv("TESTBED_HOSTNAME")
    api_key = os.getenv("TESTBED_API_KEY")

    # Create log directory
    log_dir = os.path.join(log_dir, datetime.now().strftime("%Y%m%d-%H%M%S"))
    os.makedirs(log_dir, exist_ok=True)

    stats = LatencyStats()
    sdk = TestbedSDK(base_url=hostname, api_key=api_key)

    # Create semaphore to limit parallel executions
    semaphore = asyncio.Semaphore(parallel_limit)

    async def bounded_evaluate(instance: SWEbenchInstance):
        async with semaphore:
            return await evaluate_instance(sdk, instance, stats, log_dir)

    # Run evaluations in parallel
    tasks = [bounded_evaluate(instance) for instance in instances]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Write stats to file
    stats_file = os.path.join(log_dir, "evaluation_stats.json")
    with open(stats_file, "w") as f:
        json.dump(stats.get_summary(), f, indent=2)

    # Log summary
    summary = stats.get_summary()
    logger.info("\nEvaluation Summary:")
    logger.info(f"Total time: {summary['total_time']:.2f}s")
    logger.info(f"Instances completed: {summary['instances_completed']}")
    logger.info(f"Average setup time: {summary['avg_setup_time']:.2f}s")
    logger.info(f"Average evaluation time: {summary['avg_eval_time']:.2f}s")
    logger.info(f"Average total time per instance: {summary['avg_total_time']:.2f}s")

    return all(isinstance(r, bool) and r for r in results)


def load_lite_instances() -> List[SWEbenchInstance]:
    dataset_file = Path("swebench_dataset.json")
    if not dataset_file.exists():
        raise FileNotFoundError("swebench_dataset.json not found")

    with open(dataset_file) as f:
        data = json.load(f)

    instances = []

    for instance in data:
        if instance.get("dataset") == "princeton-nlp/SWE-bench_Lite":
            instances.append(SWEbenchInstance.model_validate(instance))

    return instances


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run parallel evaluations on Testbed API"
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=128,
        help="Number of parallel evaluations (default: 16)",
    )
    parser.add_argument(
        "--log-dir",
        type=str,
        default="logs",
        help="Directory to store logs (default: logs)",
    )

    args = parser.parse_args()

    try:
        instances = load_lite_instances()
        logger.info(f"Loaded {len(instances)} instances from SWE-bench Lite dataset")

        success = asyncio.run(
            run_parallel_evaluation(
                instances=instances, parallel_limit=args.parallel, log_dir=args.log_dir
            )
        )

        sys.exit(0 if success else 1)

    except Exception as e:
        logger.exception("Failed to run parallel evaluation")
        sys.exit(1)
