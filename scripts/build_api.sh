DOCKER_REGISTRY=${DOCKER_REGISTRY:-aorwall}
API_DOCKER_IMAGE=${DOCKER_REGISTRY}/moatless-testbed-api:latest

set -e

python scripts/save_swebench_dataset.py

docker build -t ${API_DOCKER_IMAGE} -f docker/Dockerfile.api .
docker push ${API_DOCKER_IMAGE}