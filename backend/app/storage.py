"""Object storage backends for cached novel downloads."""

from __future__ import annotations

from typing import Iterator, Protocol

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from app import config


class ObjectStorageError(RuntimeError):
    """Raised when an object storage request fails."""


class NovelObjectStorage(Protocol):
    def put_text(self, key: str, text: str) -> dict[str, str | int]:
        """Store a UTF-8 text object and return storage metadata."""

    def iter_text(self, key: str, *, chunk_size: int = 65536) -> Iterator[str]:
        """Yield a UTF-8 text object in chunks."""


class R2NovelStorage:
    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str,
        access_key_id: str,
        secret_access_key: str,
        key_prefix: str = "",
    ):
        self.bucket = bucket
        self.key_prefix = key_prefix.strip("/")
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
            config=BotoConfig(signature_version="s3v4"),
        )

    def put_text(self, key: str, text: str) -> dict[str, str | int]:
        object_key = self._object_key(key)
        body = text.encode("utf-8")
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=object_key,
                Body=body,
                ContentType="text/plain; charset=utf-8",
            )
        except (BotoCoreError, ClientError) as exc:  # pragma: no cover - network dependent.
            raise ObjectStorageError(f"Failed to upload {object_key} to R2: {exc}") from exc
        return {
            "object_key": object_key,
            "content_bytes": len(body),
        }

    def iter_text(self, key: str, *, chunk_size: int = 65536) -> Iterator[str]:
        object_key = key.lstrip("/")
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=object_key)
        except (BotoCoreError, ClientError) as exc:  # pragma: no cover - network dependent.
            raise ObjectStorageError(f"Failed to download {object_key} from R2: {exc}") from exc

        body = response["Body"]
        try:
            for chunk in body.iter_chunks(chunk_size=chunk_size):
                if chunk:
                    yield chunk.decode("utf-8")
        finally:
            body.close()

    def _object_key(self, key: str) -> str:
        normalized = key.lstrip("/")
        if not self.key_prefix:
            return normalized
        return f"{self.key_prefix}/{normalized}"


def build_object_storage_from_config() -> NovelObjectStorage | None:
    if config.CACHE_STORAGE_BACKEND != "r2":
        return None

    missing = [
        name
        for name, value in (
            ("R2_ACCOUNT_ID", config.R2_ACCOUNT_ID),
            ("R2_ACCESS_KEY_ID", config.R2_ACCESS_KEY_ID),
            ("R2_SECRET_ACCESS_KEY", config.R2_SECRET_ACCESS_KEY),
            ("R2_BUCKET", config.R2_BUCKET),
        )
        if not value
    ]
    if missing:
        missing_values = ", ".join(missing)
        raise ValueError(f"Missing R2 configuration: {missing_values}")

    endpoint_url = config.R2_ENDPOINT_URL or f"https://{config.R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
    return R2NovelStorage(
        bucket=config.R2_BUCKET,
        endpoint_url=endpoint_url,
        access_key_id=config.R2_ACCESS_KEY_ID,
        secret_access_key=config.R2_SECRET_ACCESS_KEY,
        key_prefix=config.R2_KEY_PREFIX,
    )
