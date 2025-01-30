DOCKER_REGISTRY=${DOCKER_REGISTRY:-aorwall}
IMAGE_TAG=${IMAGE_TAG:-latest}
API_DOCKER_IMAGE=${DOCKER_REGISTRY}/moatless-testbed-api:${IMAGE_TAG}

echo "Building API Docker image: ${API_DOCKER_IMAGE}"

set -e

# Check if dataset exists, download if needed
echo "Checking SWE-bench dataset..."
if ! python3 scripts/download_dataset.py; then
    echo "Error: Failed to prepare dataset"
    exit 1
fi

docker build -t ${API_DOCKER_IMAGE} -f docker/Dockerfile.api .
docker push ${API_DOCKER_IMAGE}