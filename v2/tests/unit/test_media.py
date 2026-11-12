"""Offline image, storage and provider contracts; all samples are synthetic."""

import hashlib
import io
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from PIL import Image

from v2.services.ai.media import (
    FakeMediaFetcher,
    FakeObjectStore,
    MediaCodec,
    MediaError,
)
from v2.services.ai.providers import (
    FakeExtractionProvider,
    OfflineConfigurationError,
    ProviderInvalidResponse,
    ProviderRateLimit,
    ProviderTimeout,
    guard_offline_configuration,
)


def image_bytes(fmt="PNG", size=(120, 60), *, orientation=None, noise=False):
    image = Image.effect_noise(size, 100).convert("RGB") if noise else Image.new("RGB", size, "red")
    output = io.BytesIO()
    kwargs = {}
    if orientation is not None:
        exif = Image.Exif()
        exif[274] = orientation
        kwargs["exif"] = exif
    image.save(output, format=fmt, **kwargs)
    return output.getvalue()


@pytest.mark.parametrize(
    "fmt,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")]
)
def test_codec_emits_decodable_bounded_output_and_actual_hash(fmt, mime):
    result = MediaCodec().process(image_bytes(fmt), mime, max_width=40, max_height=40)
    decoded = Image.open(io.BytesIO(result["content"]))
    decoded.load()
    assert decoded.size == (40, 20) == (result["width"], result["height"])
    assert decoded.format is not None
    assert Image.MIME[decoded.format] == result["mime_type"] == mime
    assert result["content_sha256"] == hashlib.sha256(result["content"]).hexdigest()
    assert len(result["content"]) <= 600000


def test_codec_applies_exif_orientation_and_removes_metadata():
    result = MediaCodec().process(image_bytes("JPEG", orientation=6))
    decoded = Image.open(io.BytesIO(result["content"]))
    assert decoded.size == (60, 120)
    assert not decoded.getexif()
    assert "exif" not in decoded.info


def test_codec_shrinks_to_byte_budget_instead_of_returning_oversize_data():
    result = MediaCodec().process(image_bytes(size=(250, 250), noise=True), max_bytes=2500)
    decoded = Image.open(io.BytesIO(result["content"]))
    decoded.load()
    assert len(result["content"]) <= 2500
    assert decoded.width <= 250 and decoded.height <= 250


def test_codec_preserves_png_alpha():
    output = io.BytesIO()
    Image.new("RGBA", (5, 5), (50, 100, 150, 80)).save(output, "PNG")
    result = MediaCodec().process(output.getvalue())
    assert Image.open(io.BytesIO(result["content"])).getpixel((0, 0)) == (
        50,
        100,
        150,
        80,
    )


@pytest.mark.parametrize(
    "data,mime", [(b"garbage", None), (b"", None), (image_bytes(), "image/jpeg")]
)
def test_codec_rejects_invalid_content_and_mime_disagreement(data, mime):
    with pytest.raises(MediaError):
        MediaCodec().process(data, mime)


def test_codec_rejects_unsupported_codec_and_animation():
    output = io.BytesIO()
    Image.new("RGB", (5, 5)).save(output, "BMP")
    with pytest.raises(MediaError):
        MediaCodec().process(output.getvalue())
    output = io.BytesIO()
    Image.new("RGB", (5, 5), "red").save(
        output, "PNG", save_all=True, append_images=[Image.new("RGB", (5, 5), "blue")]
    )
    with pytest.raises(MediaError):
        MediaCodec().process(output.getvalue())


def test_codec_enforces_input_byte_and_pixel_limits_before_transformation():
    data = image_bytes(size=(30, 20))
    with pytest.raises(MediaError):
        MediaCodec(max_input_bytes=len(data) - 1).process(data)
    with pytest.raises(MediaError):
        MediaCodec(max_pixels=599).process(data)


@pytest.mark.parametrize(
    "limits",
    [{"max_width": 0}, {"max_height": -1}, {"max_bytes": 1}, {"max_width": True}],
)
def test_codec_rejects_bad_limits_and_impossible_budget(limits):
    with pytest.raises(MediaError):
        MediaCodec().process(image_bytes(), **limits)


def test_object_store_immutable_keys_and_authorized_fake_fetch():
    store = FakeObjectStore(environ={})
    ref = store.put("synthetic/a.png", b"one")
    assert ref == "offline://objects/synthetic/a.png"
    assert store.put("synthetic/a.png", b"one") == ref
    assert store.get(ref) == b"one"
    with pytest.raises(MediaError):
        store.put("synthetic/a.png", b"two")
    fetcher = FakeMediaFetcher(store, authorized_refs={ref}, environ={})
    assert fetcher.fetch(ref) == b"one"
    denied = store.put("synthetic/b.png", b"two")
    with pytest.raises(MediaError):
        fetcher.fetch(denied)
    with pytest.raises(MediaError):
        store.get("offline://objects/missing")


@pytest.mark.parametrize(
    "ref",
    [
        "https://example.invalid/image.png",
        "file:///etc/passwd",
        "offline://objects/../bad",
        "offline://other/key",
    ],
)
def test_fake_fetch_and_store_reject_external_or_unsafe_references(ref):
    store = FakeObjectStore(environ={})
    with pytest.raises(MediaError):
        store.get(ref)
    with pytest.raises(MediaError):
        FakeMediaFetcher(store, environ={}).fetch(ref)


@pytest.mark.parametrize("key", ["../a", "/a", "a//b", "a?token=x", "a#b", "a\\b", ""])
def test_object_store_rejects_noncanonical_keys(key):
    with pytest.raises(MediaError):
        FakeObjectStore(environ={}).put(key, b"synthetic")


@pytest.mark.parametrize(
    "env",
    [
        {"OPENROUTER_API_KEY": "synthetic-not-a-secret"},
        {"OPENAI_BASE_URL": "https://example.invalid"},
        {"TELEGRAM_SESSION": "synthetic-session"},
        {"AWS_ACCESS_KEY_ID": "synthetic"},
        {"R2_ENDPOINT_URL": "https://example.invalid"},
        {"EF_V2_PROVIDER": "openrouter"},
        {"EF_V2_STORAGE_ENDPOINT": "https://example.invalid"},
    ],
)
def test_guard_rejects_any_nonempty_external_provider_configuration(env):
    with pytest.raises(OfflineConfigurationError):
        guard_offline_configuration(env)
    for constructor in (FakeExtractionProvider, FakeObjectStore):
        with pytest.raises(OfflineConfigurationError):
            constructor(environ=env)


def test_guard_checks_current_environment_on_each_call(monkeypatch):
    provider = FakeExtractionProvider(environ={})
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic")
    with pytest.raises(OfflineConfigurationError):
        FakeExtractionProvider()
    # Explicit environment injection is an offline test seam, not global mutation.
    assert provider.extract("Synthetic listing")["description"] == "Synthetic listing"


@pytest.mark.parametrize("mode", ["success", "fallback"])
def test_fake_extraction_output_validates_against_existing_listing_schema(mode):
    schema_path = Path(__file__).resolve().parents[3] / "contracts/v2/schemas/types.v1.schema.json"
    schema = json.loads(schema_path.read_text())["$defs"]["listing"]
    result = FakeExtractionProvider(environ={}).extract(
        "Synthetic 2 rooms USD 500 monthly", mode=mode
    )
    Draft202012Validator(schema).validate(result)
    assert result["description"] == "Synthetic 2 rooms USD 500 monthly"
    assert result["contact_ref"] is None
    if mode == "success":
        assert result["rooms"] == 2
        assert result["price"] == 500
        assert result["currency"] == "USD"
        assert result["price_period"] == "monthly"
    else:
        assert result["rooms"] is None and result["price"] is None


@pytest.mark.parametrize(
    "text,price,currency,period",
    [
        ("SYNTHETIC rent 100 USD 2 rooms", 100, "USD", "monthly"),
        ("SYNTHETIC sale USD 20000", 20000, "USD", "one_time"),
        ("SYNTHETIC daily 250000 UZS", 250000, "UZS", "daily"),
    ],
)
def test_fake_provider_recognizes_synthetic_amount_order_and_listing_period(
    text, price, currency, period
):
    listing = FakeExtractionProvider(environ={}).extract(text)
    assert (listing["price"], listing["currency"], listing["price_period"]) == (
        price,
        currency,
        period,
    )


def test_guard_allows_only_explicitly_synthetic_bot_key_and_lab_credentials():
    guard_offline_configuration(
        {
            "EFV2_MODE": "FAKE_ONLY",
            "EFV2_FAKE_BOT_TOKEN": "synthetic:" + "a" * 48,
            "EFV2_PASSWORD_AI": "synthetic-db-password",
        }
    )
    with pytest.raises(OfflineConfigurationError):
        guard_offline_configuration({"EFV2_FAKE_BOT_TOKEN": "non-synthetic-forbidden"})
    with pytest.raises(OfflineConfigurationError):
        guard_offline_configuration({"EFV2_MODE": "LIVE"})


@pytest.mark.parametrize(
    "mode,error",
    [
        ("invalid", ProviderInvalidResponse),
        ("rate_limit", ProviderRateLimit),
        ("timeout", ProviderTimeout),
    ],
)
def test_provider_failures_are_explicit_and_deterministic(mode, error):
    with pytest.raises(error) as caught:
        FakeExtractionProvider(environ={}).extract("Synthetic listing", mode=mode)
    assert "Synthetic listing" not in str(caught.value)
    if mode == "rate_limit":
        assert caught.value.retry_after == 5


def test_provider_caps_input_and_rejects_unsupported_modes_and_live_media():
    provider = FakeExtractionProvider(environ={})
    for text, kwargs in [
        ("x" * 4001, {}),
        ("Synthetic", {"mode": "real"}),
        ("Synthetic", {"media": "https://example.invalid/media"}),
    ]:
        with pytest.raises(ValueError):
            provider.extract(text, **kwargs)
