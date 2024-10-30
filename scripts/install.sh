#!/bin/bash

# Export the variables so envsubst can use them
export TESTBED_NAMESPACE=${KUBERNETES_NAMESPACE:-testbeds}
export DOCKER_REGISTRY=${DOCKER_REGISTRY:-aorwall}
export IMAGE_TAG=${IMAGE_TAG:-latest}
export ENABLE_EXEC=${ENABLE_EXEC:-false}

set -e

# Generate a random API key if not provided
if [ -z "$TESTBED_API_KEY" ]; then
    echo "Generating a random API key..."
    export TESTBED_API_KEY=$(openssl rand -hex 32)
fi

echo "Installing with configuration:"
echo "  Namespace: $TESTBED_NAMESPACE"
echo "  Docker Registry: $DOCKER_REGISTRY"
echo "  Image Tag: $IMAGE_TAG"
echo "  API Key: $TESTBED_API_KEY"
echo "  Enable Exec: $ENABLE_EXEC"
echo "---"

kubectl apply -f <(envsubst < k8s/api-keys-secret.yaml)
kubectl apply -f <(envsubst < k8s/api-rbac.yaml)
kubectl apply -f <(envsubst < k8s/testbed-rbac.yaml)
kubectl apply -f <(envsubst < k8s/api-deployment.yaml)
kubectl apply -f <(envsubst < k8s/api-service.yaml)

echo "---"
echo "Waiting for external IP (this might take a few minutes)..."
while true; do
    export TESTBED_API_IP=$(kubectl get service testbed-api-service -n $TESTBED_NAMESPACE -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null)
    if [ -n "$TESTBED_API_IP" ]; then
        echo "API is available at: http://$TESTBED_API_IP"
        
        # Save IP to a file for later use
        echo "export TESTBED_API_IP=$TESTBED_API_IP" > .env.testbed
        echo "export TESTBED_HOSTNAME=http://$TESTBED_API_IP" >> .env.testbed
        echo "export TESTBED_NAMESPACE=$TESTBED_NAMESPACE" >> .env.testbed
        echo "export TESTBED_API_KEY=$TESTBED_API_KEY" >> .env.testbed
        
        break
    fi
    echo -n "."
    sleep 5
done

echo "---"
echo "Checking health endpoint http://$TESTBED_API_IP/health (this might take a few minutes)..."
curl "http://$TESTBED_API_IP/health"

echo "Installation complete!"
