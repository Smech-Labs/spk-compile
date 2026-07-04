#!/bin/bash
# SmechOS sovereign build wrapper
# Usage: ./build.sh [profile] [--phase PHASE]
#   profile defaults to smechos-plasma-live
#   Sources cache: /mnt/spk-compile-sources  (persistent, survives rebuilds)
#   Build output:  /mnt/smechos_build_root   (persistent)

set -e

PROFILE="${1:-smechos-plasma-live}"
PHASE_ARG="${@:2}"
IMAGE="smechos-builder:latest"
SOURCES="/mnt/spk-compile-sources"
TARGET="/mnt/smechos_build_root"

# Create persistent volumes if they don't exist
sudo mkdir -p "$SOURCES" "$TARGET"

echo "[build.sh] Building Docker image..."
docker build -t "$IMAGE" "$(dirname "$0")"

echo "[build.sh] Running profile: $PROFILE $PHASE_ARG"
docker run --rm \
    --privileged \
    -v "$SOURCES:/mnt/spk-compile-sources" \
    -v "$TARGET:/mnt/smechos_build_root" \
    -v "/tmp/smechos_build:/tmp/smechos_build" \
    "$IMAGE" "$PROFILE" $PHASE_ARG
