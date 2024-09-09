DOCKER_REGISTRY=${DOCKER_REGISTRY:-aorwall}
API_DOCKER_IMAGE=${DOCKER_REGISTRY}/moatless-testbed-api:latest
TESTBED_DOCKER_IMAGE=${DOCKER_REGISTRY}/moatless-testbed-sidecar:latest

docker build -t ${API_DOCKER_IMAGE} -f docker/Dockerfile.api .
docker push ${API_DOCKER_IMAGE}

docker build -t ${TESTBED_DOCKER_IMAGE} -f docker/Dockerfile.testbed .
docker push ${TESTBED_DOCKER_IMAGE}
