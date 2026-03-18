#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:-enclave-image}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
DOCKERFILE_PATH="${DOCKERFILE_PATH:-enclave/Dockerfile.enclave}"
EIF_PATH="${ENCLAVE_EIF_PATH:-enclave.eif}"

mkdir -p "$(dirname "$EIF_PATH")"

echo "[1/2] Building enclave Docker image ${IMAGE_NAME}:${IMAGE_TAG}"
docker build -f "$DOCKERFILE_PATH" -t "${IMAGE_NAME}:${IMAGE_TAG}" .

echo "[2/2] Building EIF at ${EIF_PATH}"
BUILD_OUTPUT="$(nitro-cli build-enclave --docker-uri "${IMAGE_NAME}:${IMAGE_TAG}" --output-file "$EIF_PATH")"
printf '%s\n' "$BUILD_OUTPUT"

PCR0="$({ printf '%s\n' "$BUILD_OUTPUT"; } | python3 - <<'PY'
import json
import re
import sys

text = sys.stdin.read()
match = re.search(r'(\{\s*"Measurements".*\})', text, re.DOTALL)
if not match:
    raise SystemExit(1)
measurements = json.loads(match.group(1))
print(measurements["Measurements"]["PCR0"])
PY
)"

echo
echo "EIF build complete: ${EIF_PATH}"
echo "PCR0: ${PCR0}"
echo
echo "Next steps:"
echo "1. Export ENCLAVE_PCR0_HASH=${PCR0}"
echo "2. Run ./scripts/setup_kms.sh to create the PCR-bound KMS key policy"
echo "3. Run ./scripts/run_enclave.sh to boot the enclave and start the KMS vsock proxy"
