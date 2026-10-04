import base64
import hashlib
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, Encoding, pkcs12
from cryptography.x509.oid import NameOID
from PIL import Image

from app import wallet_signer
from app.wallet_band import render_name_band
from app.wallet_signer import generate_apple_pkpass

P12_PASSWORD = "test-password"
CARD = {
    "uuid": "2b53403d-2248-47d7-90f6-fc1b2ad3429a",
    "fullName": "عضو تجريبي",
    "themeId": "gdg-blue",
    "uniId": "451000000",
}

# Drawing Arabic needs Pillow's RAQM layout, which loads the system libfribidi.
# Where that is missing the pass falls back to native fields (covered below).
can_draw_text = render_name_band("gdg-blue", "عضو", "عضو تجريبي", (17, 24, 39)) is not None
needs_text_layout = pytest.mark.skipif(not can_draw_text, reason="Pillow RAQM layout (libfribidi) is not available")


def _self_signed(common_name: str) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return key, certificate


@pytest.fixture
def signing_certs(monkeypatch: pytest.MonkeyPatch) -> None:
    key, certificate = _self_signed("Pass Type ID: test")
    _, wwdr = _self_signed("Test WWDR")
    p12 = pkcs12.serialize_key_and_certificates(
        b"pass", key, certificate, None, BestAvailableEncryption(P12_PASSWORD.encode())
    )
    monkeypatch.setenv("APPLE_P12_BASE64", base64.b64encode(p12).decode())
    monkeypatch.setenv("APPLE_P12_PASSWORD", P12_PASSWORD)
    monkeypatch.setenv("APPLE_WWDR_BASE64", base64.b64encode(wwdr.public_bytes(Encoding.DER)).decode())


def _unpack(pkpass: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(pkpass)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


@needs_text_layout
def test_the_name_is_drawn_into_the_strip_and_kept_off_the_front_fields(signing_certs) -> None:
    files = _unpack(generate_apple_pkpass(CARD))
    pass_json = json.loads(files["pass.json"])

    # Any front field would be drawn by Wallet on top of the baked name.
    assert set(pass_json["storeCard"]) == {"backFields"}
    assert pass_json["storeCard"]["backFields"][0]["value"] == CARD["fullName"]

    for suffix, size in {"": (375, 144), "@2x": (750, 288), "@3x": (1125, 432)}.items():
        assert Image.open(io.BytesIO(files[f"strip{suffix}.png"])).size == size

    other = _unpack(generate_apple_pkpass({**CARD, "fullName": "اسم آخر مختلف"}))
    assert other["strip@3x.png"] != files["strip@3x.png"]


def test_the_pass_carries_the_ios_27_poster_layout_alongside_the_store_card(signing_certs) -> None:
    files = _unpack(generate_apple_pkpass(CARD))
    pass_json = json.loads(files["pass.json"])

    assert pass_json["posterGeneric"]["primaryFields"][0] == {"key": "member_name", "value": CARD["fullName"]}
    for name in ("artwork", "primaryLogo", "logo", "icon", "strip"):
        for suffix in ("", "@2x", "@3x"):
            assert f"{name}{suffix}.png" in files


def test_the_manifest_hashes_every_packed_file(signing_certs) -> None:
    files = _unpack(generate_apple_pkpass(CARD))
    manifest = json.loads(files["manifest.json"])

    assert set(manifest) == set(files) - {"manifest.json", "signature"}
    for name, digest in manifest.items():
        assert hashlib.sha1(files[name]).hexdigest() == digest


def test_without_text_layout_the_name_falls_back_to_a_native_field(signing_certs, monkeypatch) -> None:
    monkeypatch.setattr(wallet_signer, "render_name_band", lambda *args: None)

    files = _unpack(generate_apple_pkpass(CARD))
    store_card = json.loads(files["pass.json"])["storeCard"]

    assert store_card["secondaryFields"][0]["value"] == CARD["fullName"]
    assert "primaryFields" not in store_card
    assert Image.open(io.BytesIO(files["strip@3x.png"])).size == (1125, 432)


@needs_text_layout
def test_a_long_name_is_shrunk_to_fit_the_strip() -> None:
    long_name = "عبدالرحمن عبدالعزيز عبدالمحسن عبدالكريم العبدالرحمن الطويل جدا"
    band = render_name_band("gdg-blue", "عضو", long_name, (17, 24, 39))
    assert band is not None

    image = Image.open(io.BytesIO(band["@3x"])).convert("RGB")
    surface = image.getpixel((2, 300))
    # The text is centred, so a margin column at the name's height stays untouched.
    assert all(image.getpixel((20, y)) == surface for y in range(230, 300))


def test_an_unknown_theme_gets_the_default_card(signing_certs) -> None:
    files = _unpack(generate_apple_pkpass({**CARD, "themeId": "not-a-theme"}))
    assert json.loads(files["pass.json"])["backgroundColor"] == "rgb(191, 242, 255)"
