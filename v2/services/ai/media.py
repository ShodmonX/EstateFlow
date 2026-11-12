"""Bounded real image transforms and explicit offline media adapters.

No Telegram reference hash is described as a perceptual hash. The returned
SHA-256 identifies the actual encoded output bytes, suitable for immutable keys.
"""

from __future__ import annotations

import hashlib
import io
import re
import warnings
from collections.abc import Mapping, Set
from typing import Any, Protocol

from PIL import Image, ImageOps, UnidentifiedImageError

from .providers import guard_offline_configuration


class MediaError(ValueError):
    """Unsafe, unsupported, unavailable or unauthorized media."""


class MediaCodec:
    """Decode PNG/JPEG/WebP, orient, strip metadata and enforce output bounds."""

    _FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}

    def __init__(self, *, max_input_bytes: int = 20 * 1024 * 1024, max_pixels: int = 40_000_000):
        self.max_input_bytes = self._positive_integer(max_input_bytes)
        self.max_pixels = self._positive_integer(max_pixels)

    @staticmethod
    def _positive_integer(value: int) -> int:
        if type(value) is not int or value <= 0:
            raise MediaError("image limits must be positive integers")
        return value

    def process(
        self,
        data: bytes,
        mime_type: str | None = None,
        max_width: int = 1024,
        max_height: int = 1024,
        max_bytes: int = 600000,
    ) -> dict[str, Any]:
        max_width, max_height, max_bytes = map(
            self._positive_integer, (max_width, max_height, max_bytes)
        )
        if not isinstance(data, bytes) or not data or len(data) > self.max_input_bytes:
            raise MediaError("image input byte limit or type violation")
        try:
            # Header dimensions are inspected before image.load() allocates a
            # decoded pixel buffer. Do not change Pillow's global bomb limits.
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as source:
                    fmt = source.format
                    if fmt not in self._FORMATS:
                        raise MediaError("unsupported image format")
                    output_mime = self._FORMATS[fmt]
                    if mime_type is not None and mime_type != output_mime:
                        raise MediaError("declared image MIME type disagrees with content")
                    if source.width * source.height > self.max_pixels:
                        raise MediaError("decoded image pixel limit exceeded")
                    if getattr(source, "n_frames", 1) != 1:
                        raise MediaError("animated images require an explicit frame policy")
                    source.load()
                    oriented = ImageOps.exif_transpose(source)
                    if oriented is None:
                        raise MediaError("orientation_failed")
                    mode = (
                        "RGBA"
                        if fmt in {"PNG", "WEBP"}
                        and ("A" in oriented.getbands() or "transparency" in oriented.info)
                        else "RGB"
                    )
                    # Paste into a fresh image so EXIF, ICC, comments and other
                    # input metadata do not survive output encoding.
                    image = Image.new(mode, oriented.size)
                    image.paste(oriented.convert(mode))
        except MediaError:
            raise
        except (
            UnidentifiedImageError,
            OSError,
            SyntaxError,
            ValueError,
            Image.DecompressionBombWarning,
            Image.DecompressionBombError,
        ) as exc:
            raise MediaError("image decoding rejected") from exc
        image.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
        while True:
            qualities = (85, 65, 45, 25) if fmt in {"JPEG", "WEBP"} else (None,)
            for quality in qualities:
                output = io.BytesIO()
                options: dict[str, Any] = {"optimize": True}
                if quality is not None:
                    options["quality"] = quality
                image.save(output, format=fmt, **options)
                content = output.getvalue()
                if len(content) <= max_bytes:
                    return {
                        "content": content,
                        "mime_type": output_mime,
                        "width": image.width,
                        "height": image.height,
                        "content_sha256": hashlib.sha256(content).hexdigest(),
                    }
            if image.size == (1, 1):
                raise MediaError("encoded image cannot fit output byte budget")
            image = image.resize(
                (max(1, image.width * 3 // 4), max(1, image.height * 3 // 4)),
                Image.Resampling.LANCZOS,
            )


class ObjectStore(Protocol):
    """Owner-local storage boundary; immutable references never contain tokens."""

    def put(self, key: str, data: bytes) -> str: ...
    def get(self, reference: str) -> bytes: ...


def _key(key: str) -> str:
    if not isinstance(key, str) or len(key) > 512 or not re.fullmatch(r"[A-Za-z0-9_./-]+", key):
        raise MediaError("invalid offline storage key")
    if any(part in {"", ".", ".."} for part in key.split("/")):
        raise MediaError("noncanonical offline storage key")
    return key


def _reference_key(reference: str) -> str:
    prefix = "offline://objects/"
    if not isinstance(reference, str) or not reference.startswith(prefix):
        raise MediaError("only offline object references are accepted")
    return _key(reference[len(prefix) :])


class FakeObjectStore:
    """In-memory test store; process restart durability is intentionally absent."""

    def __init__(self, *, environ: Mapping[str, str] | None = None, **configuration: Any):
        guard_offline_configuration(environ, **configuration)
        if configuration:
            raise MediaError("offline storage accepts no endpoint configuration")
        self._environ = environ
        self._objects: dict[str, bytes] = {}

    def put(self, key: str, data: bytes) -> str:
        guard_offline_configuration(self._environ)
        key = _key(key)
        if not isinstance(data, bytes):
            raise MediaError("offline object data must be bytes")
        previous = self._objects.setdefault(key, data)
        if previous != data:
            raise MediaError("immutable offline storage key conflict")
        return f"offline://objects/{key}"

    def get(self, reference: str) -> bytes:
        guard_offline_configuration(self._environ)
        key = _reference_key(reference)
        try:
            return self._objects[key]
        except KeyError:
            raise MediaError("offline media reference unavailable") from None


class FakeMediaFetcher:
    """Explicit reference allowlist stands in for a future media capability.

    This adapter cannot load files, URLs or Telegram sessions. Authorization is
    deny-by-default. Tests must explicitly supply their permitted object refs.
    """

    def __init__(
        self,
        store: ObjectStore,
        *,
        authorized_refs: Set[str] = frozenset(),
        environ: Mapping[str, str] | None = None,
        **configuration: Any,
    ):
        guard_offline_configuration(environ, **configuration)
        if configuration:
            raise MediaError("offline fetcher accepts no session configuration")
        for reference in authorized_refs:
            _reference_key(reference)
        self._store = store
        self._authorized_refs = frozenset(authorized_refs)
        self._environ = environ

    def fetch(self, reference: str) -> bytes:
        guard_offline_configuration(self._environ)
        _reference_key(reference)
        if reference not in self._authorized_refs:
            raise MediaError("offline media reference unauthorized")
        return self._store.get(reference)
