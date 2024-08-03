import json
import logging
import os
import uuid
from pathlib import Path

from testbed.container.kubernetes import KubernetesContainer
from testbed.schema import EvaluationResult
from testbed.storage.azure_blob import AzureBlobStorage
from testbed.swebench.run_evaluation import run_instance, EvaluationError


logger = logging.getLogger(__name__)


class Testbed:

    def __init__(self, testbed_id: str):
        self.testbed_id = testbed_id
        self.container = KubernetesContainer()
        self.storage = AzureBlobStorage()

    def run_evaluation(self, run_id: str) -> EvaluationResult:
        instance_file_path = os.getenv("CONFIG_FILE_PATH", "/etc/config/config.json")
        logger.info(f"Reading instance from {instance_file_path}")

        if not os.path.exists(instance_file_path):
            raise FileNotFoundError(f"Instance file not found at {instance_file_path}")

        with open(instance_file_path, 'r') as f:
            instance = json.load(f)

        instance_id = instance["instance_id"]
        remote_dir = f"{run_id}/{instance_id}"

        prediction = self.storage.fetch_prediction(remote_dir)
        if not prediction:
            prediction = {
                "model_patch": instance["patch"],
                "instance_id": instance_id,
                "model_name_or_path": uuid.uuid4().hex,
            }
            logger.info(f"Running evaluation with gold prediction")

        run_id = prediction["model_name_or_path"]
        log_dir = Path("/tmp/") / run_id / instance_id

        try:
            if not os.path.exists(log_dir):
                os.makedirs(log_dir)
            else:
                logger.info(f"Cleaning up log directory {log_dir}")
                for file in os.listdir(log_dir):
                    file_path = os.path.join(log_dir, file)
                    try:
                        if os.path.isfile(file_path):
                            os.unlink(file_path)
                    except Exception as e:
                        logger.error(f"Error deleting file {file_path}: {e}")

            report = run_instance(container=self.container, instance=instance, pred=prediction, log_dir=log_dir,
                                  timeout=1800)
            if instance_id not in report:
                logger.error(f"Instance {instance_id} not found in report: {report}")
                status = "error"
            else:
                status = _get_report_status(report[instance_id])

            return report
        except EvaluationError as e:
            logger.warning(f"Error running instance {instance_id}: {e}")
            status = e.status
        except Exception as e:
            logger.exception(f"Error running instance {instance_id}")
            status = "error"
        finally:
            logger.info(f"Instance {instance_id} finished with status {status}")
            try:
                remote_dir = f"{run_id}/{instance_id}"
                self.storage.store_dir(local_dir=log_dir, remote_dir=remote_dir)
            except Exception as e:
                logger.exception(f"Error uploading evaluation logs: {e}")


def _get_report_status(report: dict):
    if report["resolved"]:
        return "resolved"
    elif report["patch_successfully_applied"]:
        return "failed"
    else:
        return "invalid"
