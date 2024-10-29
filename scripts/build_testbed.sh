DOCKER_REGISTRY=${DOCKER_REGISTRY:-aorwall}
TESTBED_DOCKER_IMAGE=${DOCKER_REGISTRY}/moatless-testbed-sidecar:latest

docker build -t ${TESTBED_DOCKER_IMAGE} -f docker/Dockerfile.testbed .
docker push ${TESTBED_DOCKER_IMAGE}
