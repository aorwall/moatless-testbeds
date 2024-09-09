#!/bin/bash

DOCKER_IMAGE=${DOCKER_REGISTRY:-aorwall/moatless-testbed-api}
NAMESPACE=${NAMESPACE:-default}

docker build -t ${DOCKER_IMAGE}:latest -f Dockerfile.api .
docker push ${DOCKER_IMAGE}:latest

# Apply combined RBAC resources for API
kubectl apply -f <(envsubst < infra/testbed-rbac.yaml)

# Apply testbed sidecar service account and roles
kubectl apply -f <(envsubst < infra/testbed-sa.yaml)
kubectl apply -f <(envsubst < infra/testbed-role.yaml)
kubectl apply -f <(envsubst < infra/testbed-rolebinding.yaml)

kubectl apply -f <(envsubst < k8s/api-keys-secret.yaml)
kubectl apply -f <(envsubst < k8s/api-deployment.yaml)
kubectl apply -f <(envsubst < k8s/api-service.yaml)

echo "Deployment completed in namespace: $NAMESPACE"
