#!/bin/bash

# Define the source and target namespaces
SOURCE_NAMESPACE="testbeds"
TARGET_NAMESPACE="testbed-dev"

# Directory to store the ConfigMaps
CONFIGMAP_DIR="configmaps"

# Create a directory to store the ConfigMaps
mkdir -p $CONFIGMAP_DIR

# Apply each ConfigMap to the target namespace
for file in $CONFIGMAP_DIR/*.yaml; do
  # Modify the namespace in the YAML file before applying it
  sed -i "s/namespace: $SOURCE_NAMESPACE/namespace: $TARGET_NAMESPACE/g" $file
  kubectl apply -f $file -n $TARGET_NAMESPACE
  echo "Applied $(basename $file) to namespace $TARGET_NAMESPACE"
done

echo "All ConfigMaps have been applied to the target namespace."