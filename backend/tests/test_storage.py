from __future__ import annotations

from botocore.exceptions import ClientError
import pytest

from app.storage import ObjectStorageError, R2NovelStorage, _iter_decoded_utf8_chunks


class FakeStreamingBody:
    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks
        self.closed = False

    def iter_chunks(self, chunk_size: int = 65536):
        del chunk_size
        yield from self._chunks

    def close(self):
        self.closed = True


class FakeS3Client:
    def __init__(self, response=None, exc: Exception | None = None):
        self.response = response
        self.exc = exc

    def get_object(self, *, Bucket: str, Key: str):
        del Bucket, Key
        if self.exc is not None:
            raise self.exc
        return self.response


def test_iter_decoded_utf8_chunks_handles_split_multibyte_boundaries():
    text = "欢迎来到台湾。\n" * 10000
    encoded = text.encode("utf-8")
    body = FakeStreamingBody(
        [
            encoded[:65536],
            encoded[65536:98304],
            encoded[98304:],
        ]
    )

    result = "".join(_iter_decoded_utf8_chunks(body.iter_chunks(), body))

    assert result == text
    assert body.closed is True


def test_r2_iter_text_raises_before_streaming_when_get_object_fails(monkeypatch):
    def fake_client(*args, **kwargs):
        del args, kwargs
        return FakeS3Client(
            exc=ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "missing"}},
                "GetObject",
            )
        )

    monkeypatch.setattr("app.storage.boto3.client", fake_client)
    storage = R2NovelStorage(
        bucket="storybin-cache",
        endpoint_url="https://example.r2.cloudflarestorage.com",
        access_key_id="key",
        secret_access_key="secret",
        key_prefix="novels",
    )

    with pytest.raises(ObjectStorageError, match="Failed to download novels/410113/file.txt from R2"):
        storage.iter_text("novels/410113/file.txt")


def test_r2_iter_text_decodes_chunked_utf8(monkeypatch):
    text = "《二十年夏》\n作者：吟稀\n\n欢迎来到台湾。\n" * 2000
    encoded = text.encode("utf-8")
    body = FakeStreamingBody(
        [
            encoded[:65535],
            encoded[65535:131071],
            encoded[131071:],
        ]
    )

    def fake_client(*args, **kwargs):
        del args, kwargs
        return FakeS3Client(response={"Body": body})

    monkeypatch.setattr("app.storage.boto3.client", fake_client)
    storage = R2NovelStorage(
        bucket="storybin-cache",
        endpoint_url="https://example.r2.cloudflarestorage.com",
        access_key_id="key",
        secret_access_key="secret",
        key_prefix="novels",
    )

    result = "".join(storage.iter_text("novels/410113/file.txt"))

    assert result == text
    assert body.closed is True
