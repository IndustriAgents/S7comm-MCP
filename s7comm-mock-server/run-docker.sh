#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="s7-mock"
CONTAINER_NAME="s7-mock"

docker build -t "${IMAGE_NAME}" .

if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}\$"; then
  docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
fi

# Published on this machine's loopback interface only. To let other hosts
# reach the mock, publish on all interfaces instead (-p 1102:1102) or on a
# specific address (-p 192.168.0.10:1102:1102), on a trusted network only.
docker run -d --name "${CONTAINER_NAME}" -p 127.0.0.1:1102:1102 "${IMAGE_NAME}"

docker ps --filter "name=${CONTAINER_NAME}"

