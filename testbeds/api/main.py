import json
import logging
import os
import uuid
import signal
from functools import wraps
import sys
from typing import Optional, Dict, List, Any, AsyncGenerator, cast, TypedDict

from fastapi import FastAPI, HTTPException, Header, Request, Response, Depends
from fastapi.responses import JSONResponse
from opentelemetry.sdk.trace import TracerProvider
from pydantic import BaseModel
import aiohttp
from starlette.datastructures import State

from testbeds.api.manager import TestbedManager
from testbeds.schema import TestbedSummary, TestbedDetailed

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


class ClientResources(TypedDict):
    """Type for combined client resources."""

    manager: TestbedManager
    session: aiohttp.ClientSession


def get_session_from_request(request: Request) -> aiohttp.ClientSession:
    """Get the session from the request's app state."""
    if not request.app.state.session:
        raise RuntimeError("Session not initialized")
    return request.app.state.session


async def get_client_resources(request: Request) -> ClientResources:
    """Combined dependency that provides manager and session."""
    # Get manager - should be fully initialized at startup
    manager = request.app.state.manager
    if not manager:
        raise RuntimeError("TestbedManager not initialized")

    # Get session - ensure it's valid
    session = request.app.state.session
    if not session or session.closed:
        # Log session recreation event - this should be rare
        logger.warning("Session needed recreation during request")
        session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=100, ttl_dns_cache=300)
        )
        request.app.state.session = session
        await manager.set_session(session)

    return {"manager": cast(TestbedManager, manager), "session": session}


async def ensure_session(app: FastAPI) -> aiohttp.ClientSession:
    """Ensure a valid session exists and return it."""
    if not app.state.session or app.state.session.closed:
        if app.state.session and app.state.session.closed:
            # Clean up old session if needed
            await app.state.session.close()
        # Create optimized session with connection pooling
        app.state.session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=100, ttl_dns_cache=300)
        )
        if app.state.manager:
            await app.state.manager.set_session(app.state.session)
    return app.state.session


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


def configure_opentelemetry(app: FastAPI):
    if not os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"):
        logger.debug(
            "APPLICATIONINSIGHTS_CONNECTION_STRING environment variable not set"
        )
        return

    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
        from azure.monitor.opentelemetry.exporter import ApplicationInsightsSampler
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    except ImportError as e:
        logger.error(f"Failed to import Azure Monitor instrumentation. Error: {e}")
        return

    logger.info("Configuring OpenTelemetry with Azure Monitor")
    custom_sampler = ApplicationInsightsSampler(
        sampling_ratio=0.1,  # 10% sampling rate
    )

    tracer_provider = TracerProvider(sampler=custom_sampler)

    configure_azure_monitor(
        tracer_provider=tracer_provider,
    )

    FastAPIInstrumentor.instrument_app(app, excluded_urls="health")


async def validate_api_key(x_api_key: Optional[str] = Header(None)) -> str:
    api_keys = load_api_keys()
    if not api_keys:
        return "default"

    if not x_api_key:
        raise HTTPException(status_code=401, detail="API key is required")

    user_id = api_keys.get(x_api_key)
    if not user_id:
        logger.warning(f"Unauthorized access attempt - Invalid API key: {x_api_key}")
        raise HTTPException(status_code=401, detail="Invalid API key")

    return user_id


class AppConfig:
    def __init__(self):
        self.send_file_max_age_default = 0  # Disable caching
        self.enable_exec = os.environ.get("ENABLE_EXEC", "false").lower() == "true"


def create_app():
    app = FastAPI(title="Testbeds API")
    # Initialize state attributes
    app.state.session = None
    app.state.manager = None
    app.state.config = AppConfig()

    configure_opentelemetry(app)

    @app.on_event("startup")
    async def startup_event():
        try:
            logger.info("Initializing Kubernetes clients...")
            # Initialize manager
            app.state.manager = TestbedManager()
            await app.state.manager.initialize()
            logger.info("Kubernetes clients initialized successfully")

            # Create a shared aiohttp session with optimized settings and set it in the manager
            app.state.session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(limit=100, ttl_dns_cache=300)
            )
            await app.state.manager.set_session(app.state.session)
            logger.info("aiohttp client session initialized and set in manager")
        except Exception as e:
            logger.error(f"Failed to initialize clients: {e}")
            raise

    @app.on_event("shutdown")
    async def shutdown_event():
        try:
            logger.info("Closing Kubernetes clients...")
            if app.state.manager:
                await app.state.manager.close()
            logger.info("Kubernetes clients closed successfully")

            # Close the shared aiohttp session
            if app.state.session:
                await app.state.session.close()
                logger.info("aiohttp client session closed")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")

    @app.middleware("http")
    async def ensure_clients(request: Request, call_next):
        try:
            # Skip for health check endpoint
            if not request.url.path == "/health":
                # Use our optimized session management
                if not request.app.state.manager:
                    logger.info("Initializing manager in middleware")
                    request.app.state.manager = TestbedManager()
                    await request.app.state.manager.initialize()

                # Validate and potentially recreate session
                session = request.app.state.session
                if not session or session.closed:
                    logger.info("Creating new session in middleware")
                    if session and session.closed:
                        await session.close()
                    request.app.state.session = aiohttp.ClientSession(
                        connector=aiohttp.TCPConnector(limit=100, ttl_dns_cache=300)
                    )
                    await request.app.state.manager.set_session(
                        request.app.state.session
                    )

            response = await call_next(request)
            return response
        except Exception as e:
            logger.error(f"Error in client middleware: {e}")
            # Ensure session is cleaned up if there's an error
            if request.app.state.session and not request.app.state.session.closed:
                await request.app.state.session.close()
                request.app.state.session = None
            return JSONResponse(
                status_code=500,
                content={
                    "error": "Internal server error",
                    "detail": "Failed to initialize clients",
                },
            )

    @app.exception_handler(Exception)
    async def handle_exception(request: Request, exc: Exception):
        # Clear any pending alarms
        signal.alarm(0)

        reference_code = str(uuid.uuid4())

        if isinstance(exc, HTTPException):
            logger.exception(
                f"An HTTP error occurred. Reference code: {reference_code}"
            )
            return JSONResponse(
                status_code=exc.status_code,
                content={
                    "reference_code": reference_code,
                    "code": exc.status_code,
                    "error": exc.detail,
                    "description": str(exc),
                },
            )

        logger.exception(
            f"An unexpected error occurred. Reference code: {reference_code}"
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": "An unexpected error occurred",
                "reference_code": reference_code,
            },
        )

    @app.get("/health")
    async def health_check():
        return {"status": "healthy"}

    @app.get("/testbeds")
    async def list_testbeds(
        user_id: str = Depends(validate_api_key),
    ) -> List[TestbedSummary]:
        return await app.state.manager.list_testbeds(user_id)

    class TestbedCreateRequest(BaseModel):
        instance_id: str
        run_id: str | None = None

    @app.post("/testbeds")
    async def get_or_create_testbed(
        request: TestbedCreateRequest,
        user_id: str = Depends(validate_api_key),
    ) -> TestbedSummary:
        testbed = await app.state.manager.get_or_create_testbed(
            request.instance_id, user_id=user_id, run_id=request.run_id
        )
        if not testbed:
            raise HTTPException(status_code=404, detail="Failed to create testbed")
        return testbed

    @app.get("/testbeds/{testbed_id}")
    async def get_testbed(
        testbed_id: str, user_id: str = Depends(validate_api_key)
    ) -> TestbedDetailed:
        testbed = await app.state.manager.get_testbed(testbed_id, user_id)
        if not testbed:
            logger.warning(f"Testbed not found: id={testbed_id}, user_id={user_id}")
            raise HTTPException(status_code=404, detail="Testbed not found")

        return testbed

    @app.delete("/testbeds/{testbed_id}")
    async def delete_testbed(testbed_id: str, user_id: str = Depends(validate_api_key)):
        logger.info(f"delete_testbed(testbed_id={testbed_id}, user_id={user_id})")
        await app.state.manager.delete_testbed(testbed_id, user_id)
        return {"message": "Testbed killed"}

    class PatchRequest(BaseModel):
        patch: str

    @app.post("/testbeds/{testbed_id}/apply-patch")
    async def apply_patch(
        testbed_id: str,
        request: PatchRequest,
        user_id: str = Depends(validate_api_key),
        resources: ClientResources = Depends(get_client_resources),
    ):
        client = await resources["manager"].create_client(
            testbed_id, user_id, session=resources["session"]
        )
        await client.apply_patch(request.patch)
        return {"message": "Patch applied"}

    class RunTestsRequest(BaseModel):
        test_files: list[str] | None = None
        instance_id: str | None = None

    @app.post("/testbeds/{testbed_id}/run-tests")
    async def run_tests(
        testbed_id: str,
        request: RunTestsRequest,
        user_id: str = Depends(validate_api_key),
        resources: ClientResources = Depends(get_client_resources),
    ):
        logger.debug(
            f"run_tests(testbed_id={testbed_id}, user_id={user_id}, instance_id={request.instance_id})"
        )

        client = await resources["manager"].create_client(
            testbed_id, user_id, session=resources["session"]
        )
        result = await client.run_tests(request.test_files)
        return result

    @app.post("/testbeds/{testbed_id}/run-evaluation")
    async def run_evaluation(
        testbed_id: str,
        user_id: str = Depends(validate_api_key),
        resources: ClientResources = Depends(get_client_resources),
    ):
        logger.debug(f"run_evaluation(testbed_id={testbed_id}, user_id={user_id})")

        client = await resources["manager"].create_client(
            testbed_id, user_id, session=resources["session"]
        )
        result = await client.run_evaluation()
        return result

    @app.get("/testbeds/{testbed_id}/diff")
    async def get_diff(
        testbed_id: str,
        user_id: str = Depends(validate_api_key),
        resources: ClientResources = Depends(get_client_resources),
    ):
        logger.debug(f"get_diff(testbed_id={testbed_id}, user_id={user_id})")

        client = await resources["manager"].create_client(
            testbed_id, user_id, session=resources["session"]
        )
        diff = await client.get_diff()
        return {"diff": diff}

    @app.get("/testbeds/{testbed_id}/status")
    async def get_testbed_status(
        testbed_id: str, user_id: str = Depends(validate_api_key)
    ):
        logger.info(f"get_testbed_status(testbed_id={testbed_id}, user_id={user_id})")
        status = await app.state.manager.get_testbed_status(testbed_id, user_id)
        if not status:
            raise HTTPException(
                status_code=404, detail="Testbed not found or unable to read status"
            )

        return status

    @app.post("/testbeds/{testbed_id}/reset")
    async def reset_testbed(
        testbed_id: str,
        user_id: str = Depends(validate_api_key),
        resources: ClientResources = Depends(get_client_resources),
    ):
        client = await resources["manager"].create_client(
            testbed_id, user_id, session=resources["session"]
        )
        await client.reset()
        return {"message": "Testbed reset"}

    class ExecuteCommandsRequest(BaseModel):
        commands: list[str]

    @app.post("/testbeds/{testbed_id}/exec")
    async def execute_commands(
        testbed_id: str,
        request: ExecuteCommandsRequest,
        user_id: str = Depends(validate_api_key),
        resources: ClientResources = Depends(get_client_resources),
    ):
        try:
            if os.environ.get("DISABLE_COMMAND_EXECUTION", "0") == "1":
                raise HTTPException(
                    status_code=403, detail="Command execution is disabled"
                )

            client = await resources["manager"].create_client(
                testbed_id, user_id, session=resources["session"]
            )
            result = await client.execute_async(request.commands)
            return result
        except Exception as e:
            logger.exception(f"Error executing command: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/testbeds/{testbed_id}/exec")
    async def get_command_status(
        testbed_id: str,
        user_id: str = Depends(validate_api_key),
        resources: ClientResources = Depends(get_client_resources),
    ):
        client = await resources["manager"].create_client(
            testbed_id, user_id, session=resources["session"]
        )
        result = await client.get_execution_status()
        return result

    @app.get("/testbeds/{testbed_id}/health")
    async def check_testbed_health(
        testbed_id: str,
        user_id: str = Depends(validate_api_key),
        resources: ClientResources = Depends(get_client_resources),
    ):
        # First check if testbed exists and is starting up
        status = await resources["manager"].get_testbed_status(testbed_id, user_id)
        if status["status"] in ["Pending", "Unknown"]:
            return {"status": "STARTING"}

        # If running, proceed with health check
        client = await resources["manager"].create_client(
            testbed_id, user_id, session=resources["session"]
        )
        health_status = await client.check_health()
        return health_status

    @app.delete("/testbeds")
    async def delete_all_testbeds(user_id: str = Depends(validate_api_key)):
        try:
            logger.info(f"delete_all_testbeds(user_id={user_id})")
            deleted_count = await app.state.manager.delete_all_testbeds(user_id)
            logger.info(f"Deleted {deleted_count} testbeds for user {user_id}")
            return {"message": f"Deleted {deleted_count} testbeds"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/cleanup")
    async def cleanup_user_resources(user_id: str = Depends(validate_api_key)):
        logger.info(f"cleanup_user_resources(user_id={user_id})")
        deleted_count = await app.state.manager.cleanup_user_resources(user_id)
        logger.info(f"Cleaned up {deleted_count} resources for user {user_id}")
        return {"message": f"Cleaned up {deleted_count} resources"}

    @app.get("/instances/{instance_id}")
    async def get_instance(instance_id: str, user_id: str = Depends(validate_api_key)):
        """Get a SWEbench instance by ID."""
        try:
            from testbeds.swebench.utils import load_swebench_instance

            instance = await load_swebench_instance(instance_id)
            if not instance:
                raise HTTPException(
                    status_code=404, detail=f"Instance {instance_id} not found"
                )
            return instance
        except Exception as e:
            logger.exception(f"Error getting instance {instance_id}")
            raise HTTPException(status_code=500, detail=str(e))

    return app


if __name__ == "__main__":
    import uvicorn

    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
