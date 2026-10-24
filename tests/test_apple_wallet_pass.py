import hashlib
import json
import os
import struct

import pytest

from app.wallet_signer import ASSETS_DIR, generate_apple_pkpass, pass_serial_number
from tests.wallet_support import unpack as _unpack

CARD = {
    "uuid": "2b53403d-2248-47d7-90f6-fc1b2ad3429a",
    "fullName": "عضو تجريبي",
    "themeId": "gdg-blue",
    "uniId": "451000000",
}


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


def test_the_qr_is_the_cards_qr_value(signing_certs) -> None:
    qr_value = f"https://gdg-q.com/p/{CARD['uuid']}?s=AbCdEfGhIjKlMnOp"
    pass_json = json.loads(_unpack(generate_apple_pkpass({**CARD, "qrValue": qr_value}))["pass.json"])

    assert pass_json["barcodes"][0]["message"] == qr_value
    assert pass_json["barcode"]["message"] == qr_value, "the legacy key, for iOS versions before barcodes"


def test_a_card_without_a_qr_value_points_at_the_home_page(signing_certs) -> None:
    """Guest passes: their uuid is random and must never read as a member's /p/ link."""
    pass_json = json.loads(_unpack(generate_apple_pkpass(CARD))["pass.json"])

    assert pass_json["barcodes"][0]["message"] == "https://gdg-q.com"
    assert pass_json["barcode"]["message"] == "https://gdg-q.com"
    assert pass_json["serialNumber"] == f"GDGQ-{CARD['uuid'][:8].upper()}"


def test_the_serial_number_is_the_uuids_first_eight_characters_uppercased() -> None:
    """Wallet tells passes apart by it: a new format would put a second card in every member's Wallet."""
    assert pass_serial_number("2b53403d224847d790f6fc1b2ad3429a") == "GDGQ-2B53403D"
    assert pass_serial_number(CARD["uuid"]) == "GDGQ-2B53403D"


def test_without_the_web_service_the_pass_is_what_it_always_was(signing_certs) -> None:
    pass_json = json.loads(_unpack(generate_apple_pkpass(CARD))["pass.json"])

    assert "webServiceURL" not in pass_json
    assert "authenticationToken" not in pass_json
    assert [field["key"] for field in pass_json["eventTicket"]["backFields"]] == [
        "email",
        "major",
        "level",
        "club_name",
    ]
    assert not any("changeMessage" in field for field in pass_json["eventTicket"]["backFields"])


def test_the_web_service_and_the_club_message_go_into_the_pass(signing_certs) -> None:
    card = {
        **CARD,
        "webServiceURL": "https://api.example.test/wallet/apple",
        "authenticationToken": "a" * 43,
        "clubMessage": "رسائل النادي تظهر هنا",
    }
    pass_json = json.loads(_unpack(generate_apple_pkpass(card))["pass.json"])

    assert pass_json["webServiceURL"] == "https://api.example.test/wallet/apple"
    assert pass_json["authenticationToken"] == "a" * 43
    message = pass_json["eventTicket"]["backFields"][0]
    assert message == {
        "key": "club_message",
        "label": "رسالة النادي",
        "value": "رسائل النادي تظهر هنا",
        "changeMessage": "%@",
    }
    assert pass_json["serialNumber"] == "GDGQ-2B53403D"


def test_a_token_without_a_url_is_left_out(signing_certs) -> None:
    pass_json = json.loads(_unpack(generate_apple_pkpass({**CARD, "authenticationToken": "a" * 43}))["pass.json"])

    assert "webServiceURL" not in pass_json and "authenticationToken" not in pass_json
