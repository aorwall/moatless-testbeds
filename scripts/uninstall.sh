#!/bin/bash

# Load environment variables if .env.testbed exists
if [ -f .env.testbed ]; then
    source .env.testbed
fi

# Set defaults if not loaded from env file
export NAMESPACE=${NAMESPACE:-testbeds}

echo "Uninstalling from namespace: $NAMESPACE"
echo "---"

# Delete all resources
echo "Deleting API resources..."
kubectl delete service testbed-api-service 2>/dev/null || true
kubectl delete deployment testbed-api-deployment 2>/dev/null || true
kubectl delete secret testbed-api-keys-secret 2>/dev/null || true
kubectl delete role testbed-sidecar-role 2>/dev/null || true
kubectl delete rolebinding testbed-sidecar-rolebinding 2>/dev/null || true

# Delete all jobs with testbed-id label
echo "Deleting testbed jobs..."
kubectl delete jobs -l testbed-id -n $NAMESPACE 2>/dev/null || true

# Remove the environment file
if [ -f .env.testbed ]; then
    echo "Removing .env.testbed file..."
    rm .env.testbed
fi

echo "---"
echo "Uninstallation complete!" 
