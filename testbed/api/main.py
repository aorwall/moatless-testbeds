import json
import logging
import os
import uuid
from functools import wraps

from flask import Flask, request, jsonify
from opentelemetry.sdk.trace import TracerProvider
from testbed.exception import TestbedNotFoundError
from werkzeug.exceptions import HTTPException

from testbed.api.manager import TestbedManager

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


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

    configure_opentelemetry(app)

    api_keys = load_api_keys()

    testbed_manager = TestbedManager()

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
                    f"Unauthorized access attempt - Invalid API key: {api_key if api_key else 'No key provided'}"
                )
                return jsonify({"error": "Invalid API key"}), 401
            return f(user_id=user_id, *args, **kwargs)

        return decorated_function

    def get_testbed_client(testbed_id: str, user_id: str):
        client = testbed_manager.create_client(testbed_id, user_id=user_id)
        return client

    @app.errorhandler(TestbedNotFoundError)
    def handle_testbed_not_found(e):
        return jsonify({"error": "Testbed not found"}), 400

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
        logger.info(f"Testbed deleted: id={testbed_id}, user_id={user_id}")
        return jsonify({"message": "Testbed killed"}), 200

    @app.route("/testbeds/<testbed_id>/apply-patch", methods=["POST"])
    @validate_api_key
    def apply_patch(testbed_id, user_id: str):
        data = request.json
        patch = data.get("patch")
        client = get_testbed_client(testbed_id, user_id)
        if not client:
            return jsonify({"error": "Testbed not found"}), 400
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
        if not client:
            return jsonify({"error": "Testbed not found"}), 400

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

    @app.route("/testbeds/<testbed_id>/status", methods=["GET"])
    @validate_api_key
    def get_testbed_status(testbed_id: str, user_id: str):
        try:
            logger.info(f"get_testbed_status(testbed_id={testbed_id}, user_id={user_id})")
            status = testbed_manager.get_testbed_status(
                testbed_id, user_id
            )
            if not status:
                return jsonify(
                    {"error": "Testbed not found or unable to read status"}
                ), 404

            return jsonify(status), 200
        except Exception as e:
            logger.exception(f"Error getting testbed status for {testbed_id}")
            return jsonify({"error": str(e)}), 500

    @app.route("/testbeds/<testbed_id>/exec", methods=["GET"])
    @validate_api_key
    def get_command_status(testbed_id: str, user_id: str):
        try:
            client = get_testbed_client(testbed_id, user_id)
            result = client.get_execution_status()
            return jsonify(result.model_dump()), 200
        except Exception as e:
            logger.exception(f"get_command_status() Error getting command status")
            return jsonify({"error": str(e)}), 500

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
        try:
            logger.info(f"cleanup_user_resources(user_id={user_id})")
            deleted_count = testbed_manager.cleanup_user_resources(user_id)
            logger.info(f"Cleaned up {deleted_count} resources for user {user_id}")
            return jsonify({"message": f"Cleaned up {deleted_count} resources"}), 200
        except Exception as e:
            logger.exception(f"Error during cleanup for user {user_id}")
            return jsonify({"error": str(e)}), 500

    @app.route("/testbeds/<testbed_id>/diff", methods=["GET"])
    @validate_api_key
    def get_diff(testbed_id: str, user_id: str):
        try:
            client = get_testbed_client(testbed_id, user_id)
            if not client:
                return jsonify({"error": "Testbed not found"}), 400
            diff = client.get_diff()
            return jsonify({"diff": diff}), 200
        except Exception as e:
            logger.exception(f"Error getting git diff for testbed {testbed_id}")
            return jsonify({"error": str(e)}), 500

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
        if isinstance(e, HTTPException):
            error_id = str(uuid.uuid4())
            return jsonify({
                "error_id": error_id,
                "code": e.code,
                "name": e.name,
                "description": e.description
            }), e.code
        
        # Handle non-HTTP exceptions
        reference_code = str(uuid.uuid4())

        logger.exception(f"An unexpected error occurred. Reference code: {reference_code}")
        return jsonify({"error": "An unexpected error occurred", "reference_code": reference_code}), 500

    return app

if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), threaded=True)
