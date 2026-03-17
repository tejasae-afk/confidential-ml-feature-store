#!/usr/bin/env bash
set -euo pipefail

EIF_PATH="${ENCLAVE_EIF_PATH:-build/confidential-ml-feature-store.eif}"
ENCLAVE_NAME="${ENCLAVE_NAME:-confidential-ml-inference}"
ENCLAVE_CID="${ENCLAVE_CID:-16}"
ENCLAVE_CPU_COUNT="${ENCLAVE_CPU_COUNT:-2}"
ENCLAVE_MEMORY_MIB="${ENCLAVE_MEMORY_MIB:-2048}"
ENCLAVE_DEBUG_MODE="${ENCLAVE_DEBUG_MODE:-false}"

cmd=(
  nitro-cli run-enclave
  --enclave-name "$ENCLAVE_NAME"
  --cpu-count "$ENCLAVE_CPU_COUNT"
  --memory "$ENCLAVE_MEMORY_MIB"
  --eif-path "$EIF_PATH"
  --enclave-cid "$ENCLAVE_CID"
)

if [[ "$ENCLAVE_DEBUG_MODE" == "true" ]]; then
  cmd+=(--debug-mode)
fi

"${cmd[@]}"
