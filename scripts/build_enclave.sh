#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:-confidential-ml-enclave}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
DOCKERFILE_PATH="${DOCKERFILE_PATH:-enclave/Dockerfile.enclave}"
EIF_PATH="${ENCLAVE_EIF_PATH:-build/confidential-ml-feature-store.eif}"
PRIVATE_KEY="${PRIVATE_KEY:-}"
SIGNING_CERTIFICATE="${SIGNING_CERTIFICATE:-}"

mkdir -p "$(dirname "$EIF_PATH")"

docker build -f "$DOCKERFILE_PATH" -t "${IMAGE_NAME}:${IMAGE_TAG}" .

cmd=(
  nitro-cli build-enclave
  --docker-uri "${IMAGE_NAME}:${IMAGE_TAG}"
  --output-file "$EIF_PATH"
)

if [[ -n "$PRIVATE_KEY" && -n "$SIGNING_CERTIFICATE" ]]; then
  cmd+=(--private-key "$PRIVATE_KEY" --signing-certificate "$SIGNING_CERTIFICATE")
fi

"${cmd[@]}"

echo "EIF build complete: $EIF_PATH"
echo "Capture PCR measurements from the Nitro CLI output and store them securely."
