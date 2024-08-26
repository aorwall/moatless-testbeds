import json
import logging
import os
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from testbed.swebench.constants import (
    APPLY_PATCH_FAIL,
    APPLY_PATCH_PASS,
)
from testbed.container import Container

from testbed.schema import EvaluationResult, Prediction, SWEbenchInstance
from testbed.swebench.grading import get_pred_report
from testbed.swebench.test_spec import make_test_spec

logger = logging.getLogger(__name__)


class EvaluationError(Exception):
    def __init__(self, instance_id, message, status):
        super().__init__(message)
        self.instance_id = instance_id
        self.status = status


def setup_logger(instance_id: str, log_file: Path, mode="w"):
    """
    This logger is used for logging the build process of images and containers.
    It writes logs to the log file.
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"{instance_id}.{log_file.name}")
    handler = logging.FileHandler(log_file, mode=mode)
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    setattr(logger, "log_file", log_file)
    return logger


def close_logger(logger):
    # To avoid too many open files
    for handler in logger.handlers:
        handler.close()
        logger.removeHandler(handler)


def run_instance(
    container: Container,
    instance: SWEbenchInstance,
    patch: str,
    log_dir: Path,
    timeout: int = 1800,
    shared_dir: Path = Path("/shared"),
) -> EvaluationResult:
    test_spec = make_test_spec(instance)

    # Set up logging directory
    instance_id = test_spec.instance_id

    log_file = log_dir / "run_instance.log"
    file_logger = setup_logger(instance_id, log_file)
    logger.info(f"Logging to {log_file}")

    report_path = log_dir / "report.json"

    # Run the instance
    try:
        # Copy model prediction as patch file to container
        patch_file = shared_dir / "patch.diff"
        patch_file.write_text(patch)
        file_logger.info(
            f"Intermediate patch for {instance_id} written to {patch_file}, now applying to container..."
        )

        # Attempt to apply patch to container
        val = container.exec_run("git apply -v /shared/patch.diff")
        if val.exit_code != 0:
            file_logger.info(
                f"Failed to apply patch with `git apply -v`:\n {val.output}"
            )
            file_logger.info(f"Try again with `patch --batch --fuzz=5 -p1 -i`...")

            # try "patch --batch --fuzz=5 -p1 -i {patch_path}" to try again
            val = container.exec_run("patch --batch --fuzz=5 -p1 -i /shared/patch.diff")
            if val.exit_code != 0:
                file_logger.info(f"{APPLY_PATCH_FAIL}:\n{val.output}")
                raise EvaluationError(
                    instance_id,
                    f"{APPLY_PATCH_FAIL}:\n{val.output}",
                    "apply_patch_fail",
                )
            else:
                file_logger.info(f"{APPLY_PATCH_PASS}:\n{val.output}")
        else:
            file_logger.info(f"{APPLY_PATCH_PASS}:\n{val.output}")

        # Get git diff before running eval script
        try:
            git_diff_output_before = container.exec_run("git diff").output.strip()
            file_logger.info(f"Git diff before:\n{git_diff_output_before}")
        except Exception as e:
            file_logger.warning(f"Failed to get git diff before running eval script")
            logger.warning(f"Failed to get git diff before running eval script: {e}")
            git_diff_output_before = None

        eval_file = shared_dir / "eval.sh"
        eval_file.write_text(test_spec.eval_script)
        os.chmod(eval_file, 0o755)  # rwxr-xr-x permissions
        file_logger.info(f"Eval script for {instance_id} written to /eval.sh")

        # Run eval script, write output to logs
        start_time = datetime.now()
        test_output_path = log_dir / "test_output.txt"

        try:
            logger.info(f"Running eval script for {instance_id}")
            result = container.run_eval(test_output_path, timeout)
            total_runtime = (datetime.now() - start_time).total_seconds()
            file_logger.info(f"Test runtime: {total_runtime:_.2f} seconds")
        except TimeoutError as e:
            total_runtime = (datetime.now() - start_time).total_seconds()
            logger.error(f"Test timed out after {total_runtime:_.2f} seconds")
            file_logger.error(f"Test timed out after {total_runtime:_.2f} seconds")
            raise e

        try:
            # Get git diff after running eval script
            git_diff_output_after = container.execute("git diff").output.strip()

            # Check if git diff changed after running eval script
            file_logger.info(f"Git diff after:\n{git_diff_output_after}")
            if (
                git_diff_output_before
                and git_diff_output_after != git_diff_output_before
            ):
                file_logger.info(f"Git diff changed after running eval script")
        except Exception as e:
            file_logger.error(f"Failed to get git diff after running eval script")
            logger.warning(f"Failed to get git diff after running eval script: {e}")

        # Get report from test output
        file_logger.info(f"Grading answer for {instance_id}...")
        result = get_pred_report(
            test_spec=test_spec,
            log_path=test_output_path,
            include_tests_status=True,
        )
        file_logger.info(
            f"report: {result}\n"
            f"Result for {instance_id}: resolved: {result.resolved}"
        )

        # Write report to report.json
        with open(report_path, "w") as f:
            json.dump(result.to_dict(), f, indent=4)

        return result
    except EvaluationError as e:
        logger.error(f"Failed to run evaluation. Error: {e}")
        raise e
    except Exception as e:
        traceback.print_exc()
        error_msg = (
            f"Error in evaluating model for {instance_id}: {e}\n"
            f"{traceback.format_exc()}\n"
        )
        file_logger.info(error_msg)
        raise e
    finally:
        close_logger(file_logger)
