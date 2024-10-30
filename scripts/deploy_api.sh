#!/bin/bash

export NAMESPACE=${KUBERNETES_NAMESPACE:-testbeds}
export DOCKER_REGISTRY=${DOCKER_REGISTRY:-aorwall}
export IMAGE_TAG=${IMAGE_TAG:-$(git rev-parse --short HEAD)}

echo "Deploying API to namespace: $NAMESPACE with image tag: $IMAGE_TAG"

scripts/build_api.sh

kubectl apply -f <(envsubst < k8s/api-deployment.yaml)
kubectl apply -f <(envsubst < k8s/api-service.yaml)

echo "Deployment completed in namespace: $NAMESPACE with image tag: $IMAGE_TAG"
