import json
import logging
import os
import uuid
import signal
from functools import wraps

from flask import Flask, request, jsonify
from opentelemetry.sdk.trace import TracerProvider
from werkzeug.exceptions import HTTPException

from testbeds.api.manager import TestbedManager
from testbeds.exceptions import TestbedNotFoundError

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


class WorkerTimeoutException(Exception):
    pass


def timeout_handler(signum, frame):
    raise WorkerTimeoutException("Request timed out")


def load_api_keys():
    api_keys_path = os.environ.get("API_KEYS_PATH", "/app/api_keys.json")
    try:
        with open(api_keys_path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error(f"API keys file not found at {api_keys_path}")
        return {}
    except json.JSONDecodeError:
        logger.error(f"Failed to parse API keys JSON from {api_keys_path}")
        return {}


def configure_opentelemetry(app):
    if not os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"):
        logger.debug(
            "APPLICATIONINSIGHTS_CONNECTION_STRING environment variable not set"
        )
        return

    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
        from azure.monitor.opentelemetry.exporter import ApplicationInsightsSampler
        from opentelemetry.instrumentation.flask import FlaskInstrumentor
    except ImportError:
        logger.error("Failed to import Azure Monitor instrumentation")
        return

    logger.info("Configuring OpenTelemetry with Azure Monitor")
    custom_sampler = ApplicationInsightsSampler(
        sampling_ratio=0.1,  # 10% sampling rate
    )

    tracer_provider = TracerProvider(sampler=custom_sampler)

    configure_azure_monitor(
        tracer_provider=tracer_provider,
    )

    FlaskInstrumentor().instrument_app(app, excluded_urls="health")


def create_app():
    app = Flask(__name__)
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0  # Disable caching
    app.config["PROPAGATE_EXCEPTIONS"] = True
    app.config["ENABLE_EXEC"] = os.environ.get("ENABLE_EXEC", "false").lower() == "true"

    configure_opentelemetry(app)

    api_keys = load_api_keys()

    testbed_manager = TestbedManager()

    @app.before_request
    def setup_timeout():
        # Setup the timeout handler
        signal.signal(signal.SIGALRM, timeout_handler)
        # Set the alarm
        signal.alarm(30)  # 30 seconds timeout

    @app.after_request
    def clear_timeout(response):
        # Clear the alarm
        signal.alarm(0)
        return response

    @app.errorhandler(WorkerTimeoutException)
    def handle_timeout_error(e):
        logger.error("Request timed out", exc_info=True)
        return jsonify({
            "error": "Request timed out",
            "code": 504,
            "reference_code": str(uuid.uuid4())
        }), 504

    @app.errorhandler(Exception)
    def handle_exception(e):
        # Clear any pending alarms
        signal.alarm(0)
        
        reference_code = str(uuid.uuid4())
        
        if isinstance(e, WorkerTimeoutException):
            logger.error(f"Request timed out. Reference code: {reference_code}", exc_info=True)
            return jsonify({
                "error": "Request timed out",
                "reference_code": reference_code,
                "code": 504
            }), 504
        
        if isinstance(e, HTTPException):
            logger.exception(f"An HTTP error occurred. Reference code: {reference_code}")
            return jsonify({
                "reference_code": reference_code,
                "code": e.code,
                "error": e.name,
                "description": e.description
            }), e.code

        logger.exception(f"An unexpected error occurred. Reference code: {reference_code}")
        return jsonify({
            "error": "An unexpected error occurred", 
            "reference_code": reference_code
        }), 500

    def validate_api_key(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not api_keys:
                # If no API keys are configured, use default user
                return f(user_id="default", *args, **kwargs)

            api_key = request.headers.get("X-API-Key")
            user_id = api_keys.get(api_key)
            if not user_id:
                logger.warning(
                    f"Unauthorized access attempt - Invalid API key: {api_key if api_key else 'No key provided'} "
                    f"for endpoint: {request.path} [{request.method}]"
                )
                return jsonify({"error": "Invalid API key"}), 401
            return f(user_id=user_id, *args, **kwargs)

        return decorated_function

    def get_testbed_client(testbed_id: str, user_id: str):
        client = testbed_manager.create_client(testbed_id, user_id=user_id)
        return client

    @app.route("/health", methods=["GET"])
    def health_check():
        return jsonify({"status": "healthy"}), 200

    @app.route("/testbeds", methods=["GET"])
    @validate_api_key
    def list_testbeds(user_id):
        return jsonify(testbed_manager.list_testbeds(user_id)), 200

    @app.route("/testbeds", methods=["POST"])
    @validate_api_key
    def get_or_create_testbed(user_id):
        data = request.json
        instance_id = data.get("instance_id")
        if not instance_id:
            return jsonify({"error": "Missing instance_id parameter"}), 400
        run_id = data.get("run_id")

        testbed = testbed_manager.get_or_create_testbed(
            instance_id, user_id=user_id, run_id=run_id
        )
        return jsonify(testbed.model_dump()), 200

    @app.route("/testbeds/<testbed_id>", methods=["GET"])
    @validate_api_key
    def get_testbed(testbed_id, user_id: str):
        testbed = testbed_manager.get_testbed(testbed_id, user_id)
        if not testbed:
            logger.warning(f"Testbed not found: id={testbed_id}, user_id={user_id}")
            return jsonify({"error": "Testbed not found"}), 404

        return jsonify(testbed.model_dump()), 200

    @app.route("/testbeds/<testbed_id>", methods=["DELETE"])
    @validate_api_key
    def delete_testbed(testbed_id: str, user_id: str):
        logger.info(f"delete_testbed(testbed_id={testbed_id}, user_id={user_id})")
        testbed_manager.delete_testbed(testbed_id, user_id)
        return jsonify({"message": "Testbed killed"}), 200

    @app.route("/testbeds/<testbed_id>/apply-patch", methods=["POST"])
    @validate_api_key
    def apply_patch(testbed_id, user_id: str):
        data = request.json
        patch = data.get("patch")
        client = get_testbed_client(testbed_id, user_id)
        client.apply_patch(patch)
        return jsonify({"message": "Patch applied"}), 200

    @app.route("/testbeds/<testbed_id>/run-tests", methods=["POST"])
    @validate_api_key
    def run_tests(testbed_id, user_id: str):
        data = request.json
        test_files = data.get("test_files")
        instance_id = data.get("instance_id")

        logger.debug(
            f"run_tests(testbed_id={testbed_id}, user_id={user_id}, instance_id={instance_id})"
        )

        client = get_testbed_client(testbed_id, user_id)
        result = client.run_tests(test_files)
        return jsonify(result.model_dump()), 200

    @app.route("/testbeds/<testbed_id>/run-evaluation", methods=["POST"])
    @validate_api_key
    def run_evaluation(testbed_id: str, user_id: str):
        logger.debug(
            f"run_evaluation(testbed_id={testbed_id}, user_id={user_id})"
        )

        client = get_testbed_client(testbed_id, user_id)
        result = client.run_evaluation()
        return jsonify(result.model_dump()), 200

    @app.route("/testbeds/<testbed_id>/diff", methods=["GET"])
    @validate_api_key
    def get_diff(testbed_id: str, user_id: str):
        logger.debug(f"get_diff(testbed_id={testbed_id}, user_id={user_id})")

        client = get_testbed_client(testbed_id, user_id)
        diff = client.get_diff()
        return jsonify({"diff": diff}), 200

    @app.route("/testbeds/<testbed_id>/status", methods=["GET"])
    @validate_api_key
    def get_testbed_status(testbed_id: str, user_id: str):
        logger.info(f"get_testbed_status(testbed_id={testbed_id}, user_id={user_id})")
        status = testbed_manager.get_testbed_status(
            testbed_id, user_id
        )
        if not status:
            return jsonify(
                {"error": "Testbed not found or unable to read status"}
            ), 404

        return jsonify(status), 200

    @app.route("/testbeds/<testbed_id>/exec", methods=["POST"])
    @validate_api_key
    def execute_commands(testbed_id: str, user_id: str):
        if not app.config["ENABLE_EXEC"]:
            return jsonify({"error": "Command execution is disabled"}), 403

        data = request.json
        commands = data.get("commands")
        if not commands or not isinstance(commands, list):
            return jsonify({"error": "Missing or invalid commands parameter"}), 400

        client = get_testbed_client(testbed_id, user_id)
        result = client.execute_async(commands)
        return jsonify(result.model_dump()), 200

    @app.route("/testbeds/<testbed_id>/exec", methods=["GET"])
    @validate_api_key
    def get_command_status(testbed_id: str, user_id: str):
        client = get_testbed_client(testbed_id, user_id)
        result = client.get_execution_status()
        return jsonify(result.model_dump()), 200

    @app.route("/testbeds/<testbed_id>/health", methods=["GET"])
    @validate_api_key
    def check_testbed_health(testbed_id: str, user_id: str):
        logger.debug(f"check_testbed_health(testbed_id={testbed_id}, user_id={user_id})")
        
        # First check if testbed exists and is starting up
        status = testbed_manager.get_testbed_status(testbed_id, user_id)
        if status["status"] in ["Pending", "Unknown"]:
            return jsonify({"status": "STARTING"}), 200
            
        # If running, proceed with health check
        client = get_testbed_client(testbed_id, user_id)
        health_status = client.check_health()
        return jsonify(health_status), 200 if health_status["status"] == "OK" else 503

    @app.route("/testbeds", methods=["DELETE"])
    @validate_api_key
    def delete_all_testbeds(user_id: str):
        try:
            logger.info(f"delete_all_testbeds(user_id={user_id})")
            deleted_count = testbed_manager.delete_all_testbeds(user_id)
            logger.info(f"Deleted {deleted_count} testbeds for user {user_id}")
            return jsonify({"message": f"Deleted {deleted_count} testbeds"}), 200
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/cleanup", methods=["POST"])
    @validate_api_key
    def cleanup_user_resources(user_id: str):
        logger.info(f"cleanup_user_resources(user_id={user_id})")
        deleted_count = testbed_manager.cleanup_user_resources(user_id)
        logger.info(f"Cleaned up {deleted_count} resources for user {user_id}")
        return jsonify({"message": f"Cleaned up {deleted_count} resources"}), 200

    @app.errorhandler(TestbedNotFoundError)
    def handle_testbed_not_found(e):
        return jsonify({"error": "Testbed not found"}), 400

    @app.errorhandler(404)
    def handle_404(e):
        error_id = str(uuid.uuid4())
        return jsonify({
            "error_id": error_id,
            "code": 404,
            "name": "Not Found",
            "description": str(e)
        }), 404

    @app.errorhandler(Exception)
    def handle_exception(e):
        reference_code = str(uuid.uuid4())
        if isinstance(e, HTTPException):
            logger.exception(f"An http error occurred. Reference code: {reference_code}")
            return jsonify({
                "reference_code": reference_code,
                "code": e.code,
                "error": e.name,
                "description": e.description
            }), e.code

        reference_code = str(uuid.uuid4())
        logger.exception(f"An unexpected error occurred. Reference code: {reference_code}")
        return jsonify({"error": "An unexpected error occurred", "reference_code": reference_code}), 500

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), threaded=True)
