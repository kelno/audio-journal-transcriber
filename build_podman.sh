#!/bin/bash
set -e  # Exit immediately if any command fails

# Check if a version argument is provided
if [ -z "$1" ]; then
    echo "Usage: $0 <version>"
    exit 1
fi

VERSION="$1"

echo "Building Podman image..."
podman build --build-arg VERSION="$VERSION" -t "audio-journal-transcriber:$VERSION" -t "audio-journal-transcriber:latest" .
echo "Build completed."
