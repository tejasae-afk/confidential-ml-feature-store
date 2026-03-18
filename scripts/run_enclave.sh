#!/usr/bin/env bash
set -euo pipefail

EIF_PATH="${ENCLAVE_EIF_PATH:-enclave.eif}"
ENCLAVE_CPU_COUNT="${ENCLAVE_CPU_COUNT:-2}"
ENCLAVE_MEMORY_MIB="${ENCLAVE_MEMORY_MIB:-512}"
ENCLAVE_CID="${ENCLAVE_CID:-16}"
ENCLAVE_NAME="${ENCLAVE_NAME:-confidential-ml-inference}"
AWS_REGION="${AWS_REGION:-us-east-1}"
KMS_PROXY_PORT="${KMS_PROXY_PORT:-8000}"

RUN_OUTPUT="$(nitro-cli run-enclave \
  --eif-path "$EIF_PATH" \
  --cpu-count "$ENCLAVE_CPU_COUNT" \
  --memory "$ENCLAVE_MEMORY_MIB" \
  --enclave-cid "$ENCLAVE_CID" \
  --enclave-name "$ENCLAVE_NAME")"
printf '%s\n' "$RUN_OUTPUT"

echo
echo "Enclave requested CID: ${ENCLAVE_CID}"
echo "Starting Nitro Enclaves KMS vsock proxy on parent-instance CID 3 port ${KMS_PROXY_PORT}"
if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files | grep -q '^nitro-enclaves-vsock-proxy.service'; then
  sudo systemctl enable nitro-enclaves-vsock-proxy.service
  sudo systemctl start nitro-enclaves-vsock-proxy.service
  echo "Started nitro-enclaves-vsock-proxy.service"
else
  echo "nitro-enclaves-vsock-proxy.service was not found on this host." >&2
  echo "Install Nitro CLI and ensure the vsock proxy service is available before using KMS from the enclave." >&2
fi

echo
echo "Useful commands:"
echo "- nitro-cli describe-enclaves"
echo "- nitro-cli console --enclave-name ${ENCLAVE_NAME}"
echo "- Ensure the enclave uses KMS proxy endpoint 3:${KMS_PROXY_PORT} for attested KMS operations"
