import logging
import os

from azure.core.credentials import AzureKeyCredential
from azure.core.messaging import CloudEvent
from azure.eventgrid import EventGridPublisherClient

from events import Events

logger = logging.getLogger(__name__)


class EventGridEvents(Events):

    def __init__(self):
        event_grid_endpoint = os.environ.get("AZURE_EVENT_GRID_ENDPOINT")
        access_key = os.environ.get("AZURE_EVENT_GRID_ACCESS_KEY")

        if not event_grid_endpoint or not access_key:
            self.client = None
        else:
            self.client = EventGridPublisherClient(event_grid_endpoint, credential=AzureKeyCredential(access_key))
            logger.info(f"Azure Event Grid client created for {event_grid_endpoint}")

    def send_start_event(self, run_id: str, instance_id: str):
        if not self.client:
            logger.warning("Azure Event Grid client not available. Skipping event.")
            return

        event = CloudEvent(
            source="swebench-runner",
            subject=run_id,
            type="swebench.evaluation.started_instance",
            data={"instance_id": instance_id},
        )

        logger.info(f"send_start_event: {event}")
        self.client.send(event)

    def send_finish_event(self, run_id: str, instance_id: str, status: str):
        if not self.client:
            logger.warning("Azure Event Grid client not available. Skipping event.")
            return

        event = CloudEvent(
            source="swebench-runner",
            subject=run_id,
            type="swebench.evaluation.finished_instance",
            data={"instance_id": instance_id, "status": status},
        )

        logger.info(f"send_finish_event: {event}")
        self.client.send(event)
