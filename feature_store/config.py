"""Application configuration for the feature store."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Self
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_LOG_LEVELS: set[str] = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_AWS_REGION_PATTERN = re.compile(r"^[a-z]{2}-[a-z0-9-]+-\d+$")
_DDB_TABLE_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,255}$")


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables.

    Attributes:
        aws_region: AWS region used for boto3 clients and resources.
        dynamodb_table_name: DynamoDB table that stores tenant and feature-set records.
        dynamodb_endpoint: Optional local endpoint for DynamoDB Local.
        kms_key_id: KMS key identifier for future confidential inference flows.
        enclave_cid: CID used to communicate with a Nitro Enclave over vsock.
        enclave_port: Vsock port exposed by the enclave service.
        log_level: Global log level for structured logging.
    """

    aws_region: str = Field(default="us-east-1", alias="AWS_REGION")
    dynamodb_table_name: str = Field(
        default="confidential-ml-feature-store",
        alias="DYNAMODB_TABLE_NAME",
    )
    dynamodb_endpoint: str | None = Field(default=None, alias="DYNAMODB_ENDPOINT")
    kms_key_id: str = Field(default="placeholder-kms-key", alias="KMS_KEY_ID")
    enclave_cid: int = Field(default=16, alias="ENCLAVE_CID")
    enclave_port: int = Field(default=5005, alias="ENCLAVE_PORT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    use_mock_enclave: bool = Field(default=False, alias="USE_MOCK_ENCLAVE")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("aws_region")
    @classmethod
    def validate_aws_region(cls, value: str) -> str:
        """Validate the AWS region format.

        Args:
            value: Raw AWS region value.

        Returns:
            The normalized AWS region string.

        Raises:
            ValueError: If the region is empty or malformed.
        """
        normalized = value.strip()
        if not normalized:
            raise ValueError("AWS_REGION must not be empty.")
        if not _AWS_REGION_PATTERN.match(normalized):
            raise ValueError("AWS_REGION must look like 'us-east-1'.")
        return normalized

    @field_validator("dynamodb_table_name")
    @classmethod
    def validate_table_name(cls, value: str) -> str:
        """Validate the DynamoDB table name.

        Args:
            value: Raw table name.

        Returns:
            The normalized table name.

        Raises:
            ValueError: If the table name is invalid.
        """
        normalized = value.strip()
        if not _DDB_TABLE_PATTERN.match(normalized):
            raise ValueError(
                "DYNAMODB_TABLE_NAME must be 3-255 characters and contain only "
                "letters, numbers, underscores, hyphens, or periods.",
            )
        return normalized

    @field_validator("dynamodb_endpoint", mode="before")
    @classmethod
    def normalize_dynamodb_endpoint(cls, value: object) -> str | None:
        """Normalize the optional DynamoDB endpoint.

        Args:
            value: Raw endpoint value.

        Returns:
            A normalized endpoint URL or ``None``.
        """
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return str(value)

    @field_validator("dynamodb_endpoint")
    @classmethod
    def validate_dynamodb_endpoint(cls, value: str | None) -> str | None:
        """Validate the DynamoDB endpoint URL.

        Args:
            value: Normalized endpoint URL.

        Returns:
            The validated endpoint URL.

        Raises:
            ValueError: If the endpoint URL is invalid.
        """
        if value is None:
            return None
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("DYNAMODB_ENDPOINT must be a valid http(s) URL.")
        return value

    @field_validator("kms_key_id")
    @classmethod
    def validate_kms_key_id(cls, value: str) -> str:
        """Validate that the KMS key identifier is present.

        Args:
            value: Raw key identifier.

        Returns:
            The normalized key identifier.

        Raises:
            ValueError: If the key identifier is empty.
        """
        normalized = value.strip()
        if not normalized:
            raise ValueError("KMS_KEY_ID must not be empty.")
        return normalized

    @field_validator("enclave_cid")
    @classmethod
    def validate_enclave_cid(cls, value: int) -> int:
        """Validate the enclave CID.

        Args:
            value: CID integer.

        Returns:
            The validated CID.

        Raises:
            ValueError: If the CID is outside the valid vsock range.
        """
        if value <= 2:
            raise ValueError("ENCLAVE_CID must be greater than 2.")
        return value

    @field_validator("enclave_port")
    @classmethod
    def validate_enclave_port(cls, value: int) -> int:
        """Validate the enclave port number.

        Args:
            value: Port integer.

        Returns:
            The validated port number.

        Raises:
            ValueError: If the port is outside the valid TCP/vsock range.
        """
        if not 1 <= value <= 65535:
            raise ValueError("ENCLAVE_PORT must be between 1 and 65535.")
        return value

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        """Validate the configured logging level.

        Args:
            value: Raw log level.

        Returns:
            The normalized uppercase log level.

        Raises:
            ValueError: If the log level is unsupported.
        """
        normalized = value.strip().upper()
        if normalized not in _LOG_LEVELS:
            raise ValueError(
                f"LOG_LEVEL must be one of: {', '.join(sorted(_LOG_LEVELS))}.",
            )
        return normalized

    @model_validator(mode="after")
    def validate_local_endpoint_compatibility(self) -> Self:
        """Perform cross-field validation.

        Returns:
            The validated settings instance.

        Raises:
            ValueError: If local endpoint configuration is invalid.
        """
        if self.dynamodb_endpoint and self.dynamodb_endpoint.startswith("https://localhost"):
            raise ValueError(
                "DYNAMODB_ENDPOINT should usually use http:// for local development.",
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached application settings.

    Returns:
        A validated ``Settings`` instance.
    """
    return Settings()
