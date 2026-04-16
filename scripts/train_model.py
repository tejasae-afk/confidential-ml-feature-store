"""Train and envelope-encrypt a sample scikit-learn model artifact."""

from __future__ import annotations

import argparse
import base64
import json
import os
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import boto3
import joblib
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sklearn.datasets import load_iris
from sklearn.ensemble import RandomForestClassifier


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments.

    Returns:
        Parsed CLI arguments.
    """
    parser = argparse.ArgumentParser(
        description="Train and envelope-encrypt a sample model artifact.",
    )
    parser.add_argument(
        "--tenant-id",
        required=True,
        help="Tenant ID used for KMS encryption context.",
    )
    parser.add_argument("--model-name", default="iris-rf", help="Logical model name.")
    parser.add_argument(
        "--output-dir",
        default=os.getenv("MODEL_STORAGE_DIR", "artifacts"),
        help="Directory where encrypted artifacts will be written.",
    )
    parser.add_argument(
        "--kms-key-id",
        default=os.getenv("KMS_KEY_ID", "arn:aws:kms:us-east-1:749382610573:key/mrk-f3c9e2b741d865a0f4821c3d096e57b8"),
        help="AWS KMS key ID or ARN.",
    )
    parser.add_argument(
        "--aws-region",
        default=os.getenv("AWS_REGION", "us-east-1"),
        help="AWS region for KMS.",
    )
    parser.add_argument(
        "--n-estimators",
        type=int,
        default=50,
        help="Number of trees in the RandomForestClassifier.",
    )
    return parser.parse_args()


def main() -> None:
    """Train a model and persist encrypted artifacts."""
    args = parse_args()
    output_directory = Path(args.output_dir).resolve() / args.tenant_id / args.model_name
    output_directory.mkdir(parents=True, exist_ok=True)

    model = _train_model(args.n_estimators)
    serialized_model = _serialize_model(model)

    kms_client = boto3.client("kms", region_name=args.aws_region)
    plaintext_key, encrypted_key = _generate_data_key(
        kms_client=kms_client,
        kms_key_id=args.kms_key_id,
        tenant_id=args.tenant_id,
    )

    aad = f"{args.tenant_id}:{args.model_name}".encode()
    nonce = os.urandom(12)
    ciphertext = AESGCM(plaintext_key).encrypt(nonce, serialized_model, aad)

    encrypted_model_payload: dict[str, Any] = {
        "algorithm": "AESGCM",
        "tenant_id": args.tenant_id,
        "model_name": args.model_name,
        "feature_dimension": int(getattr(model, "n_features_in_", 0)),
        "nonce_b64": base64.b64encode(nonce).decode("ascii"),
        "ciphertext_b64": base64.b64encode(ciphertext).decode("ascii"),
        "aad_b64": base64.b64encode(aad).decode("ascii"),
        "trained_at": datetime.now(UTC).isoformat(),
        "framework": "scikit-learn",
        "serializer": "joblib",
    }

    encrypted_model_path = output_directory / "encrypted_model.bin"
    encrypted_key_path = output_directory / "encrypted_data_key.bin"
    encrypted_model_path.write_bytes(
        json.dumps(encrypted_model_payload, separators=(",", ":")).encode("utf-8"),
    )
    encrypted_key_path.write_bytes(encrypted_key)

    print(f"Encrypted model artifact written to: {encrypted_model_path}")
    print(f"Encrypted data key written to: {encrypted_key_path}")
    print()
    print("Next steps:")
    print(f"1. Upload both files to S3, or keep them on the host under: {output_directory}")
    print(
        "2. Ensure the enclave can receive these bytes over vsock during the first predict request."
    )
    print("3. Record the model name and tenant ID when invoking POST /inference/.")


def _train_model(n_estimators: int) -> RandomForestClassifier:
    """Train a simple classifier on the Iris dataset.

    Args:
        n_estimators: Number of trees.

    Returns:
        Trained classifier.
    """
    iris = load_iris()
    model = RandomForestClassifier(
        n_estimators=n_estimators,
        random_state=42,
    )
    model.fit(iris.data, iris.target)
    return model


def _serialize_model(model: RandomForestClassifier) -> bytes:
    """Serialize a scikit-learn model with joblib.

    Args:
        model: Trained model.

    Returns:
        Serialized model bytes.
    """
    buffer = BytesIO()
    joblib.dump(model, buffer)
    return buffer.getvalue()


def _generate_data_key(
    *,
    kms_client: Any,
    kms_key_id: str,
    tenant_id: str,
) -> tuple[bytes, bytes]:
    """Generate a tenant-bound KMS data key.

    Args:
        kms_client: Boto3 KMS client.
        kms_key_id: KMS key identifier.
        tenant_id: Tenant identifier.

    Returns:
        Tuple of ``(plaintext_key, encrypted_key)``.
    """
    response = kms_client.generate_data_key(
        KeyId=kms_key_id,
        KeySpec="AES_256",
        EncryptionContext={"tenant_id": tenant_id},
    )
    return bytes(response["Plaintext"]), bytes(response["CiphertextBlob"])


if __name__ == "__main__":
    main()
