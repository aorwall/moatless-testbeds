#!/bin/bash

IMAGE_NAME="testbed-sidecar"
ACR_NAME="moatless"
NEW_VERSION="latest"

echo "Logging in to Azure Container Registry..."
az acr login --name ${ACR_NAME}

echo "Building Docker image..."
docker build -t ${ACR_NAME}.azurecr.io/${IMAGE_NAME}:${NEW_VERSION} .
docker push ${ACR_NAME}.azurecr.io/${IMAGE_NAME}:${NEW_VERSION}
