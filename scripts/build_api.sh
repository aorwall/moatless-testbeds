DOCKER_REGISTRY=${DOCKER_REGISTRY:-aorwall}
IMAGE_TAG=${IMAGE_TAG:-latest}
API_DOCKER_IMAGE=${DOCKER_REGISTRY}/moatless-testbed-api:${IMAGE_TAG}

echo "Building API Docker image: ${API_DOCKER_IMAGE}"

set -e

python scripts/save_swebench_dataset.py

docker build -t ${API_DOCKER_IMAGE} -f docker/Dockerfile.api .
docker push ${API_DOCKER_IMAGE}