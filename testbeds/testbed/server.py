import base64
import logging
import os
import time
import uuid

from flask import Flask, request, jsonify
from kubernetes import client
from opentelemetry import trace
from opentelemetry.propagate import extract
from opentelemetry.sdk.resources import Resource

from testbeds.schema import (
    RunCommandsRequest,
)
from testbeds.testbed.kubernetes import KubernetesContainer

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(name)s [%(levelname)s] %(message)s"
)
logging.getLogger().setLevel(logging.INFO)
logging.getLogger("azure").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


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
        logger.warning(f"Failed to verify pod: {e}")
    except Exception as e:
        logger.error(f"Failed to connect to Kubernetes API: {e}")
        raise


def configure_opentelemetry(app):
    connection_string = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if connection_string:
        try:
            from azure.monitor.opentelemetry import configure_azure_monitor
            from opentelemetry.instrumentation.flask import FlaskInstrumentor
        except ImportError:
            logger.error("Failed to import Azure Monitor instrumentation")
            return

        logger.info("Configuring OpenTelemetry with Azure Monitor")

        resource = Resource.create(
            attributes={
                "service.instance.id": os.environ.get("POD_NAME"),
                "service.name": "testbed",
                "service.namespace": os.environ.get("KUBE_NAMESPACE"),
                "user.id": os.environ.get("USER_ID"),
                "instance.id": os.environ.get("INSTANCE_ID"),
                "testbed.id": os.environ.get("TESTBED_ID"),
            }
        )
        configure_azure_monitor(resource=resource)

        FlaskInstrumentor().instrument_app(app, excluded_urls="health")
    else:
        logger.warning(
            "APPLICATIONINSIGHTS_CONNECTION_STRING not set. No telemetry will be sent."
        )


def create_app():
    app = Flask(__name__)

    container = KubernetesContainer()
    configure_opentelemetry(app)

    def check_container_reachability():
        while not container.is_reachable():
            time.sleep(0.1)

        return True

    @app.before_request
    def before_request():
        # Extract the trace context from the incoming request headers
        context = extract(request.headers)
        # Get the current span and set it as the current context
        span = trace.get_current_span()
        trace.set_span_in_context(span, context)

    @app.route("/health", methods=["GET"])
    def health():
        logger.debug(f"health() Health check from {request.remote_addr}")
        try:
            if check_container_reachability():
                logger.debug("health() status OK")
                return jsonify({"status": "OK"}), 200
            else:
                logger.warning("health() Container is not reachable")
                return jsonify(
                    {"status": "ERROR", "message": "Testbed container is not reachable"}
                ), 500
        except Exception as e:
            error_id = str(uuid.uuid4())
            logger.exception(
                f"health() Error checking container reachability, error_id: {error_id}"
            )
            return jsonify(
                {
                    "status": "ERROR",
                    "message": f"Error checking container reachability: {str(e)}",
                    "error_id": error_id,
                }
            ), 500

    @app.route("/exec", methods=["POST"])
    def execute_command():
        data = request.json
        run_request = RunCommandsRequest(**data)
        logger.info(f"execute_command() {run_request.commands}")

        try:
            result = container.execute(run_request.commands, run_request.timeout)
            return jsonify(result.model_dump()), 200
        except Exception as e:
            error_id = str(uuid.uuid4())
            logger.exception(
                f"execute_command() Error during execution, error_id: {error_id}"
            )
            return jsonify(
                {
                    "status": "ERROR",
                    "message": f"Error during execution: {str(e)}",
                    "error_id": error_id,
                }
            ), 500

    @app.route("/exec", methods=["GET"])
    def get_execution_status():
        try:
            result = container.get_execution_status()
            return jsonify(result.model_dump()), 200
        except Exception as e:
            error_id = str(uuid.uuid4())
            logger.exception(
                f"get_execution_status() Error retrieving status, error_id: {error_id}"
            )
            return jsonify(
                {
                    "status": "ERROR",
                    "message": f"Error retrieving status: {str(e)}",
                    "error_id": error_id,
                }
            ), 500

    @app.route("/file", methods=["GET"])
    def get_file():
        file_path = request.args.get("file_path")
        logger.info(f"get_file() Reading file: {file_path}")
        if not file_path:
            return jsonify(
                {"status": "ERROR", "message": "Missing file_path parameter"}
            ), 400

        try:
            content = container.read_file(file_path)
            encoded_content = base64.b64encode(content.encode()).decode()
            return jsonify({"status": "OK", "content": encoded_content}), 200
        except FileNotFoundError as e:
            error_id = str(uuid.uuid4())
            logger.error(
                f"get_file() File not found: {file_path}, error_id: {error_id}"
            )
            return jsonify(
                {
                    "status": "ERROR",
                    "message": f"File not found: {str(e)}",
                    "error_id": error_id,
                }
            ), 404
        except Exception as e:
            error_id = str(uuid.uuid4())
            logger.exception(f"Error reading file: {file_path}, error_id: {error_id}")
            return jsonify(
                {
                    "status": "ERROR",
                    "message": f"Error reading file: {str(e)}",
                    "error_id": error_id,
                }
            ), 500

    @app.route("/file", methods=["POST"])
    def save_file():
        data = request.json
        file_path = data.get("file_path")
        content = data.get("content")
        logger.info(f"save_file() Saving file: {file_path}")
        if not file_path or not content:
            return jsonify(
                {"status": "ERROR", "message": "Missing file_path or content"}
            ), 400

        try:
            decoded_content = base64.b64decode(content)
            container.write_file(file_path, decoded_content)
            return jsonify(
                {"status": "OK", "message": f"File saved successfully: {file_path}"}
            ), 200
        except Exception as e:
            error_id = str(uuid.uuid4())
            logger.exception(f"Error saving file: {file_path}, error_id: {error_id}")
            return jsonify(
                {
                    "status": "ERROR",
                    "message": f"Error saving file: {str(e)}",
                    "error_id": error_id,
                }
            ), 500

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=8000)
