import os
from kubernetes import config, client
import logging
from gunicorn.app.base import BaseApplication
from testbed.server.server import app

logger = logging.getLogger(__name__)


class StandaloneApplication(BaseApplication):
    def __init__(self, app, options=None):
        self.options = options or {}
        self.application = app
        super().__init__()

    def load_config(self):
        for key, value in self.options.items():
            if key in self.cfg.settings and value is not None:
                self.cfg.set(key.lower(), value)

    def load(self):
        return self.application


def check_kubernetes_connection():
    try:
        v1 = client.CoreV1Api()

        pod_name = os.environ.get("POD_NAME")
        if not pod_name:
            logger.warning("POD_NAME environment variable not set")
            return

        namespace = os.environ.get("KUBE_NAMESPACE")
        if not namespace:
            logger.warning("KUBE_NAMESPACE environment variable not set")
            return

        pod = v1.read_namespaced_pod(name=pod_name, namespace=namespace)
        logger.info(f"Successfully verified pod: {pod.metadata.name}")
    except client.exceptions.ApiException as e:
        logger.error(f"Failed to verify pod: {e}")
    except Exception as e:
        logger.error(f"Failed to connect to Kubernetes API: {e}")
        raise


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s [%(levelname)s] %(message)s"
    )
    logging.getLogger("azure").setLevel(logging.WARNING)

    try:
        config.load_incluster_config()
        logger.info("Successfully loaded in-cluster Kubernetes configuration")
        check_kubernetes_connection()
    except config.ConfigException as e:
        logger.error(f"Failed to load Kubernetes configuration: {e}")
        raise

    logger.info("Starting server")

    options = {
        "bind": "0.0.0.0:8000",
        "workers": 4, 
        "timeout": 300,
        "loglevel": "info",
        "keepalive": 120,
    }

    StandaloneApplication(app, options).run()