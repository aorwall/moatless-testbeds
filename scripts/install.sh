#!/bin/bash

NAMESPACE=${KUBERNETES_NAMESPACE:-testbed-dev}
DOCKER_REGISTRY=${DOCKER_REGISTRY:-aorwall}

kubectl apply -f <(envsubst < k8s/api-rbac.yaml)
kubectl apply -f <(envsubst < k8s/testbed-rbac.yaml)

kubectl apply -f <(envsubst < k8s/api-keys-secret.yaml)
kubectl apply -f <(envsubst < k8s/api-deployment.yaml)
kubectl apply -f <(envsubst < k8s/api-service.yaml)

echo "API installed in namespace: $NAMESPACE"
