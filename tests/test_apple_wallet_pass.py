import base64
import hashlib
import io
import json
import os
import struct
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, Encoding, pkcs12
from cryptography.x509.oid import NameOID

from app.wallet_signer import ASSETS_DIR, generate_apple_pkpass

P12_PASSWORD = "test-password"
CARD = {
    "uuid": "2b53403d-2248-47d7-90f6-fc1b2ad3429a",
    "fullName": "عضو تجريبي",
    "themeId": "gdg-blue",
    "uniId": "451000000",
}


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


def _png_size(data: bytes) -> tuple[int, int]:
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def test_the_pass_is_an_event_ticket_with_the_name_as_its_headline(signing_certs) -> None:
    files = _unpack(generate_apple_pkpass(CARD))
    ticket = json.loads(files["pass.json"])["eventTicket"]

    assert ticket["primaryFields"][0]["value"] == CARD["fullName"]
    assert [field["key"] for field in ticket["secondaryFields"]] == ["uni_id", "institution"]
    assert ticket["secondaryFields"][-1]["textAlignment"] == "PKTextAlignmentRight"


def test_a_card_without_a_university_id_leaves_the_field_out(signing_certs) -> None:
    files = _unpack(generate_apple_pkpass({**CARD, "uniId": None}))
    ticket = json.loads(files["pass.json"])["eventTicket"]

    assert [field["key"] for field in ticket["secondaryFields"]] == ["institution"]


@pytest.mark.parametrize("theme_id", ["gdg-blue", "gdg-red", "gdg-gold-admin"])
def test_each_theme_packs_its_own_background_at_every_scale(signing_certs, theme_id: str) -> None:
    files = _unpack(generate_apple_pkpass({**CARD, "themeId": theme_id}))

    for suffix, size in {"": (180, 220), "@2x": (360, 440), "@3x": (540, 660)}.items():
        assert _png_size(files[f"background{suffix}.png"]) == size
        with open(os.path.join(ASSETS_DIR, f"background-{theme_id}{suffix}.png"), "rb") as f:
            assert files[f"background{suffix}.png"] == f.read()
    # A strip would replace the background: PassKit shows one or the other.
    assert not any(name.startswith("strip") for name in files)


def test_values_are_white_on_every_theme(signing_certs) -> None:
    # Wallet draws values white over a background image regardless; saying so keeps
    # the back of the pass and any future style consistent with the face.
    for theme_id in ("gdg-blue", "gdg-red", "gdg-gold-admin"):
        files = _unpack(generate_apple_pkpass({**CARD, "themeId": theme_id}))
        assert json.loads(files["pass.json"])["foregroundColor"] == "rgb(255, 255, 255)"


def test_the_manifest_hashes_every_packed_file(signing_certs) -> None:
    files = _unpack(generate_apple_pkpass(CARD))
    manifest = json.loads(files["manifest.json"])

    assert set(manifest) == set(files) - {"manifest.json", "signature"}
    for name, digest in manifest.items():
        assert hashlib.sha1(files[name]).hexdigest() == digest


def test_an_unknown_theme_gets_the_default_card(signing_certs) -> None:
    default = _unpack(generate_apple_pkpass(CARD))
    files = _unpack(generate_apple_pkpass({**CARD, "themeId": "not-a-theme"}))

    assert files["background@3x.png"] == default["background@3x.png"]
