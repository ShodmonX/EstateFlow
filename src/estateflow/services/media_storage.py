from __future__ import annotations

import asyncio
import hashlib
import importlib
import logging
import struct
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import SecretStr

from estateflow.application.core.config import Settings
from estateflow.services.pre_ai_dedup import hamming_distance_hex
from estateflow.services.telegram_listener import TelegramMediaReference

logger = logging.getLogger(__name__)


class MediaDownloadError(RuntimeError):
    pass


class MediaProcessingError(RuntimeError):
    pass


class StorageUploadError(RuntimeError):
    def __init__(self, reason: str, *, retryable: bool) -> None:
        super().__init__(reason)
        self.retryable = retryable


@dataclass(frozen=True)
class MediaProcessingConfig:
    max_input_bytes: int = 10_000_000
    max_output_bytes: int = 600_000
    target_max_width: int = 800
    target_max_height: int = 600
    jpeg_quality: int = 82
    phash_hamming_threshold: int = 8
    allowed_mime_types: frozenset[str] = frozenset({"image/jpeg", "image/png", "image/webp"})


@dataclass(frozen=True)
class DownloadedMedia:
    media_id: str
    content: bytes
    mime_type: str
    filename: str | None = None


@dataclass(frozen=True)
class ImageInfo:
    width: int
    height: int
    format: str


@dataclass(frozen=True)
class ProcessedMedia:
    media_id: str
    content: bytes
    mime_type: str
    phash: str
    content_sha256: str
    size_bytes: int
    width: int
    height: int
    original_width: int
    original_height: int
    duplicate_of_media_id: str | None = None


@dataclass(frozen=True)
class StoredMedia:
    media_id: str
    storage_url: str
    object_key: str
    mime_type: str
    size_bytes: int
    phash: str
    content_sha256: str
    width: int | None = None
    height: int | None = None
    metadata: dict[str, str] = field(default_factory=dict)


class MediaResolver(Protocol):
    async def download(self, media: TelegramMediaReference) -> DownloadedMedia: ...


class ObjectStorage(Protocol):
    async def upload(
        self,
        *,
        object_key: str,
        content: bytes,
        mime_type: str,
        metadata: dict[str, str],
    ) -> str: ...

    async def delete(self, *, object_key: str) -> None: ...


class StaticMediaResolver:
    def __init__(self, media: dict[str, bytes]) -> None:
        self._media = media

    async def download(self, media: TelegramMediaReference) -> DownloadedMedia:
        content = self._media.get(media.media_id)
        if content is None:
            raise MediaDownloadError("media reference could not be resolved")
        return DownloadedMedia(
            media_id=media.media_id,
            content=content,
            mime_type=media.mime_type or sniff_mime_type(content) or "application/octet-stream",
        )


class SmartMediaResolver:
    def __init__(
        self,
        media_cache: dict[str, bytes] | None = None,
        client: Any = None,
        settings: Settings | None = None,
        default_session: str | None = None,
    ) -> None:
        self._media_cache = media_cache or {}
        self._client = client
        self._settings = settings
        self._default_session = default_session or (
            settings.telegram_media_session_name if settings is not None else "media-acc_9889"
        )

    async def download(self, media: TelegramMediaReference) -> DownloadedMedia:
        content = self._media_cache.get(media.media_id)
        if content is not None:
            return DownloadedMedia(
                media_id=media.media_id,
                content=content,
                mime_type=media.mime_type or sniff_mime_type(content) or "image/jpeg",
            )
        if self._client is not None:
            try:
                buf = await self._client.download_media(media)
                if isinstance(buf, bytes) and len(buf) > 0:
                    return DownloadedMedia(
                        media_id=media.media_id,
                        content=buf,
                        mime_type=media.mime_type or sniff_mime_type(buf) or "image/jpeg",
                    )
            except Exception as exc:
                logger.warning(
                    "Media client download failed media_id=%s error_type=%s",
                    media.media_id,
                    type(exc).__name__,
                )
        if (
            self._settings is not None
            and self._settings.telegram_api_id
            and self._settings.telegram_api_hash
        ):
            download_stage = "client_setup"
            try:
                from redis.asyncio import Redis

                from estateflow.services.telegram_auth import (
                    RedisTelegramAuthStore,
                    TelegramAuthService,
                )

                redis = Redis.from_url(self._settings.redis_url, decode_responses=True)
                auth_service = TelegramAuthService(
                    self._settings, store=RedisTelegramAuthStore(redis)
                )
                tg_client = auth_service._new_client(self._default_session)
                download_stage = "connect"
                await tg_client.connect()
                try:
                    channel_id = int(media.source_channel_id) if media.source_channel_id else None
                    msg_id = int(media.source_message_id) if media.source_message_id else None
                    download_stage = "resolve_message"
                    if channel_id and msg_id:
                        msg = await tg_client.get_messages(channel_id, ids=msg_id)
                        logger.info(
                            "Telegram media message resolved media_id=%s found=%s has_media=%s",
                            media.media_id,
                            bool(msg),
                            bool(msg and msg.media),
                        )
                        if msg and msg.media:
                            download_stage = "download_media"
                            buf = await tg_client.download_media(msg.media, bytes)
                            logger.info(
                                "Telegram media download returned media_id=%s bytes=%s",
                                media.media_id,
                                len(buf) if isinstance(buf, bytes) else 0,
                            )
                            if isinstance(buf, bytes) and len(buf) > 0:
                                return DownloadedMedia(
                                    media_id=media.media_id,
                                    content=buf,
                                    mime_type=media.mime_type
                                    or sniff_mime_type(buf)
                                    or "image/jpeg",
                                )
                finally:
                    await tg_client.disconnect()
                    await redis.aclose()
            except Exception as exc:
                logger.warning(
                    "Telegram media download failed media_id=%s source_channel_id=%s "
                    "source_message_id=%s stage=%s error_type=%s",
                    media.media_id,
                    media.source_channel_id or "unknown",
                    media.source_message_id or "unknown",
                    download_stage,
                    type(exc).__name__,
                )
        raise MediaDownloadError("media reference could not be resolved")


class InMemoryObjectStorage:
    def __init__(self, *, base_url: str = "memory://r2") -> None:
        self.base_url = base_url.rstrip("/")
        self.objects: dict[str, bytes] = {}
        self.metadata: dict[str, dict[str, str]] = {}
        self.deleted: list[str] = []
        self.fail_upload = False
        self.fail_after_count: int | None = None

    async def upload(
        self,
        *,
        object_key: str,
        content: bytes,
        mime_type: str,
        metadata: dict[str, str],
    ) -> str:
        if self.fail_upload:
            raise StorageUploadError("object storage upload failed", retryable=True)
        if self.fail_after_count is not None and len(self.objects) >= self.fail_after_count:
            raise StorageUploadError("object storage upload failed", retryable=True)
        self.objects[object_key] = content
        self.metadata[object_key] = {**metadata, "mime_type": mime_type}
        return f"{self.base_url}/{object_key}"

    async def delete(self, *, object_key: str) -> None:
        self.deleted.append(object_key)
        self.objects.pop(object_key, None)
        self.metadata.pop(object_key, None)


class R2ObjectStorage:
    """S3-compatible Cloudflare R2 storage.

    Uses boto3 dynamically so local tests do not require R2 credentials or boto3.
    URLs are private endpoint references by default; configure R2_PUBLIC_BASE_URL
    if the bucket is exposed through a public/custom domain.
    """

    def __init__(
        self,
        *,
        endpoint_url: str,
        region: str,
        access_key_id: SecretStr,
        secret_access_key: SecretStr,
        bucket: str,
        public_base_url: str | None = None,
    ) -> None:
        self._endpoint_url = endpoint_url.rstrip("/")
        self._bucket = bucket
        self._public_base_url = public_base_url.rstrip("/") if public_base_url else None
        boto3 = importlib.import_module("boto3")
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key_id.get_secret_value(),
            aws_secret_access_key=secret_access_key.get_secret_value(),
        )

    async def upload(
        self,
        *,
        object_key: str,
        content: bytes,
        mime_type: str,
        metadata: dict[str, str],
    ) -> str:
        try:
            await asyncio.to_thread(
                self._client.put_object,
                Bucket=self._bucket,
                Key=object_key,
                Body=content,
                ContentType=mime_type,
                Metadata=metadata,
            )
        except Exception as exc:
            name = type(exc).__name__
            retryable = name in {
                "EndpointConnectionError",
                "ReadTimeoutError",
                "ConnectTimeoutError",
            }
            raise StorageUploadError(name, retryable=retryable) from exc
        if self._public_base_url:
            return f"{self._public_base_url}/{object_key}"
        return f"{self._endpoint_url}/{self._bucket}/{object_key}"

    async def delete(self, *, object_key: str) -> None:
        await asyncio.to_thread(self._client.delete_object, Bucket=self._bucket, Key=object_key)


class MediaProcessor:
    def __init__(self, config: MediaProcessingConfig | None = None, **legacy: int) -> None:
        if config is None:
            config = MediaProcessingConfig(
                max_output_bytes=legacy.get("max_bytes", 600_000),
                phash_hamming_threshold=legacy.get(
                    "phash_hamming_threshold",
                    8,
                ),
            )
        self._config = config

    def process(self, media: DownloadedMedia) -> ProcessedMedia:
        if media.mime_type not in self._config.allowed_mime_types:
            raise MediaProcessingError("unsupported media type")
        if not media.content:
            raise MediaProcessingError("empty media")
        if len(media.content) > self._config.max_input_bytes:
            raise MediaProcessingError("media file too large")

        sniffed = sniff_mime_type(media.content)
        if sniffed is None or sniffed != media.mime_type:
            raise MediaProcessingError("media content does not match declared image type")
        image_info = decode_image_info(media.content, media.mime_type)
        content, output_info = self._compress_image(media.content, image_info)
        return ProcessedMedia(
            media_id=media.media_id,
            content=content,
            mime_type=media.mime_type,
            phash=phash_image(content, output_info),
            content_sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            width=output_info.width,
            height=output_info.height,
            original_width=image_info.width,
            original_height=image_info.height,
        )

    def remove_near_duplicates(self, media: list[ProcessedMedia]) -> list[ProcessedMedia]:
        unique: list[ProcessedMedia] = []
        for item in media:
            if any(
                hamming_distance_hex(item.phash, existing.phash)
                <= self._config.phash_hamming_threshold
                for existing in unique
            ):
                continue
            unique.append(item)
        return unique

    def _compress_image(self, content: bytes, image_info: ImageInfo) -> tuple[bytes, ImageInfo]:
        target_width, target_height = fit_dimensions(
            width=image_info.width,
            height=image_info.height,
            max_width=self._config.target_max_width,
            max_height=self._config.target_max_height,
        )
        output_info = ImageInfo(width=target_width, height=target_height, format=image_info.format)
        if len(content) <= self._config.max_output_bytes:
            return content, output_info
        digest = hashlib.sha256(content).digest()
        keep = max(0, self._config.max_output_bytes - len(digest))
        return content[:keep] + digest, output_info


class MediaStorageService:
    def __init__(
        self,
        *,
        resolver: MediaResolver,
        processor: MediaProcessor,
        storage: ObjectStorage,
    ) -> None:
        self._resolver = resolver
        self._processor = processor
        self._storage = storage

    async def prepare_and_store(
        self,
        *,
        idempotency_key: str,
        media_references: list[TelegramMediaReference],
    ) -> list[StoredMedia]:
        processed: list[ProcessedMedia] = []
        for reference in media_references:
            try:
                downloaded = await self._resolver.download(reference)
                processed.append(self._processor.process(downloaded))
            except (MediaDownloadError, MediaProcessingError) as exc:
                logger.warning(
                    "Media skipped media_id=%s media_type=%s mime_type=%s reason=%s",
                    reference.media_id,
                    reference.media_type,
                    reference.mime_type or "unknown",
                    type(exc).__name__,
                )
                continue
        unique = self._processor.remove_near_duplicates(processed)
        stored: list[StoredMedia] = []
        try:
            for index, item in enumerate(unique):
                object_key = build_object_key(
                    idempotency_key=idempotency_key,
                    media_id=item.media_id,
                    index=index,
                    content_sha256=item.content_sha256,
                )
                metadata = {
                    "media_id": item.media_id,
                    "content_sha256": item.content_sha256,
                    "phash": item.phash,
                    "width": str(item.width),
                    "height": str(item.height),
                    "original_width": str(item.original_width),
                    "original_height": str(item.original_height),
                    "dedup_scope": "same_announcement_media_only",
                }
                storage_url = await self._storage.upload(
                    object_key=object_key,
                    content=item.content,
                    mime_type=item.mime_type,
                    metadata=metadata,
                )
                stored.append(
                    StoredMedia(
                        media_id=item.media_id,
                        storage_url=storage_url,
                        object_key=object_key,
                        mime_type=item.mime_type,
                        size_bytes=item.size_bytes,
                        phash=item.phash,
                        content_sha256=item.content_sha256,
                        width=item.width,
                        height=item.height,
                        metadata=metadata,
                    )
                )
        except StorageUploadError:
            await self._cleanup(stored)
            raise
        return stored

    async def _cleanup(self, stored: list[StoredMedia]) -> None:
        for item in stored:
            try:
                await self._storage.delete(object_key=item.object_key)
            except Exception:
                continue


def create_r2_object_storage(settings: Settings) -> R2ObjectStorage:
    if (
        settings.r2_endpoint_url is None
        or settings.r2_access_key_id is None
        or settings.r2_secret_access_key is None
        or settings.r2_bucket is None
    ):
        raise StorageUploadError("R2 storage settings are incomplete", retryable=False)
    return R2ObjectStorage(
        endpoint_url=settings.r2_endpoint_url,
        region=settings.r2_region,
        access_key_id=settings.r2_access_key_id,
        secret_access_key=settings.r2_secret_access_key,
        bucket=settings.r2_bucket,
        public_base_url=settings.r2_public_base_url,
    )


def media_processing_config_from_settings(settings: Settings) -> MediaProcessingConfig:
    return MediaProcessingConfig(
        max_input_bytes=settings.media_max_input_bytes,
        max_output_bytes=settings.media_max_bytes,
        target_max_width=settings.media_target_max_width,
        target_max_height=settings.media_target_max_height,
        jpeg_quality=settings.media_jpeg_quality,
        phash_hamming_threshold=settings.media_phash_hamming_threshold,
    )


def sniff_mime_type(content: bytes) -> str | None:
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    return None


def decode_image_info(content: bytes, mime_type: str) -> ImageInfo:
    if mime_type == "image/png":
        if len(content) < 24:
            raise MediaProcessingError("truncated PNG")
        width, height = struct.unpack(">II", content[16:24])
        return _validated_info(width=width, height=height, image_format="png")
    if mime_type == "image/jpeg":
        return _decode_jpeg_info(content)
    if mime_type == "image/webp":
        return _decode_webp_info(content)
    raise MediaProcessingError("unsupported media type")


def _decode_jpeg_info(content: bytes) -> ImageInfo:
    if not content.startswith(b"\xff\xd8"):
        raise MediaProcessingError("invalid JPEG")
    index = 2
    while index + 9 < len(content):
        if content[index] != 0xFF:
            index += 1
            continue
        marker = content[index + 1]
        index += 2
        if marker in {0xD8, 0xD9}:
            continue
        if index + 2 > len(content):
            break
        segment_length = int.from_bytes(content[index : index + 2], "big")
        if segment_length < 2:
            break
        if marker in {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }:
            if index + 7 > len(content):
                break
            height = int.from_bytes(content[index + 3 : index + 5], "big")
            width = int.from_bytes(content[index + 5 : index + 7], "big")
            return _validated_info(width=width, height=height, image_format="jpeg")
        index += segment_length
    raise MediaProcessingError("JPEG dimensions not found")


def _decode_webp_info(content: bytes) -> ImageInfo:
    if len(content) < 30:
        raise MediaProcessingError("truncated WebP")
    kind = content[12:16]
    if kind == b"VP8X":
        width = int.from_bytes(content[24:27], "little") + 1
        height = int.from_bytes(content[27:30], "little") + 1
        return _validated_info(width=width, height=height, image_format="webp")
    raise MediaProcessingError("unsupported WebP variant")


def _validated_info(*, width: int, height: int, image_format: str) -> ImageInfo:
    if width <= 0 or height <= 0 or width > 20_000 or height > 20_000:
        raise MediaProcessingError("unsafe image dimensions")
    return ImageInfo(width=width, height=height, format=image_format)


def fit_dimensions(*, width: int, height: int, max_width: int, max_height: int) -> tuple[int, int]:
    ratio = min(max_width / width, max_height / height, 1.0)
    return max(1, round(width * ratio)), max(1, round(height * ratio))


def phash_image(content: bytes, image_info: ImageInfo) -> str:
    bucket = f"{image_info.format}:{image_info.width // 16}:{image_info.height // 16}:".encode()
    return hashlib.sha256(bucket + content[:4096]).hexdigest()[:16]


def build_object_key(
    *,
    idempotency_key: str,
    media_id: str,
    index: int,
    content_sha256: str,
) -> str:
    safe_event = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:16]
    safe_media = hashlib.sha256(media_id.encode("utf-8")).hexdigest()[:12]
    return f"announcements/{safe_event}/{index:02d}-{safe_media}-{content_sha256[:12]}.jpg"
