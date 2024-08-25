import json
import logging
import os
import uuid
from pathlib import Path
from typing import Optional

from testbed.container.kubernetes import KubernetesContainer
from testbed.schema import  EvaluationResult, SWEbenchInstance,  CommandStatusResponse
from testbed.storage.azure_blob import AzureBlobStorage
from testbed.swebench.run_evaluation import run_instance, EvaluationError


logger = logging.getLogger(__name__)


class Testbed:

    def __init__(self, testbed_id: str):
        self.testbed_id = testbed_id
        logger.info(f"Initializing Testbed with ID: {testbed_id}")
        self.container = KubernetesContainer()
        self.storage = AzureBlobStorage()

        logger.info("Testbed initialization complete")

    def run_commands(self, commands: list[str]):
        self.container.exec_commands(commands)
        # TODO: Check for errors
        self.executing_commands = commands

    def get_run_status(self) -> CommandStatusResponse:
        is_executing = self.container.is_executing()
        output = self.container.get_output()
        commands = self.container.last_executed_commands

        return CommandStatusResponse(is_executing=is_executing, commands=commands, output=output)

    def run_evaluation(self, instance: SWEbenchInstance, run_id: Optional[str] = None, patch: Optional[str] = None, timeout: int = 1800) -> EvaluationResult:
        instance_id = instance.instance_id
        run_id = run_id or uuid.uuid4().hex

        if not patch:
            patch = instance.patch
            logger.info(f"Running evaluation with gold prediction")

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

            report = run_instance(
                container=self.container,
                instance=instance,
                patch=patch,
                log_dir=log_dir,
                timeout=timeout,
            )

            status = "resolved" if report.resolved else "failed"
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
