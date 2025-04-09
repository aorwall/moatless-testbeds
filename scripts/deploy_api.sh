#!/bin/bash

# Source environment variables from .env file
if [ -f .env ]; then
    source .env
else
    echo "Error: .env file not found"
    exit 1
fi

export NAMESPACE=${KUBERNETES_NAMESPACE:-testbeds}
export DOCKER_REGISTRY=${DOCKER_REGISTRY:-aorwall}
export IMAGE_TAG=${IMAGE_TAG:-$(git rev-parse --short HEAD)}
export SWEBENCH_DOCKER_REGISTRY=${SWEBENCH_DOCKER_REGISTRY}
export SWEBENCH_IMAGE_PREFIX=${SWEBENCH_IMAGE_PREFIX}
export ENABLE_EXEC=${ENABLE_EXEC}
export APPLICATIONINSIGHTS_CONNECTION_STRING=${APPLICATIONINSIGHTS_CONNECTION_STRING}

echo "Deploying API to namespace: $NAMESPACE with image tag: $IMAGE_TAG"

scripts/build_api.sh

kubectl apply -f <(envsubst < k8s/api-deployment.yaml)
kubectl apply -f <(envsubst < k8s/api-service.yaml)

echo "Deployment completed in namespace: $NAMESPACE with image tag: $IMAGE_TAG"
