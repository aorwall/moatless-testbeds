#!/bin/bash

DOCKER_REGISTRY=${DOCKER_REGISTRY:-aorwall}
DOCKER_IMAGE=${DOCKER_REGISTRY}/moatless-testbed-api
IMAGE_TAG=$(date +%Y%m%d-%H%M%S)
TIMESTAMP=$(date +%s)
NAMESPACE=${KUBERNETES_NAMESPACE:-testbed-dev}

# Build and push the Docker image
docker build -t ${DOCKER_IMAGE}:${IMAGE_TAG} -f docker/Dockerfile.api .
docker push ${DOCKER_IMAGE}:${IMAGE_TAG}

# Export variables for envsubst
export DOCKER_REGISTRY IMAGE_TAG TIMESTAMP NAMESPACE

# Apply the deployment using envsubst
kubectl apply -f <(envsubst < k8s/api-deployment.yaml)

echo "Upgrade completed with image tag: ${IMAGE_TAG}"