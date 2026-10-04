import base64
import hashlib
import io
import json
import logging
import os
import time
import zipfile
from typing import Any, Dict

import jwt

from app.config import config
from app.wallet_band import render_name_band
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import pkcs12, Encoding
from cryptography.hazmat.primitives.serialization.pkcs7 import PKCS7Options, PKCS7SignatureBuilder

logger = logging.getLogger(__name__)

# Base path for static wallet assets
ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets", "gdg.pass")

# Minimal 1x1 transparent PNG fallback
TRANSPARENT_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

DEFAULT_THEME = "gdg-blue"
THEMES_CONFIG = {
    "gdg-blue": {
        "bg_rgb": "rgb(191, 242, 255)",
        "fg_rgb": "rgb(17, 24, 39)",
        "text_rgb": (17, 24, 39),
        "label_rgb": "rgb(51, 65, 85)",
        "badge_color": "#BFF2FF",
        "role_title": "عضو نادي قوقل للطلبة المطورين",
    },
    "gdg-red": {
        "bg_rgb": "rgb(255, 217, 220)",
        "fg_rgb": "rgb(17, 24, 39)",
        "text_rgb": (17, 24, 39),
        "label_rgb": "rgb(75, 85, 99)",
        "badge_color": "#FFD9DC",
        "role_title": "عضو نادي قوقل للطلبة المطورين",
    },
    "gdg-gold-admin": {
        "bg_rgb": "rgb(0, 0, 0)",
        "fg_rgb": "rgb(255, 255, 255)",
        "text_rgb": (255, 255, 255),
        "label_rgb": "rgb(209, 213, 219)",
        "badge_color": "#000000",
        "role_title": "إداري نادي قوقل للطلبة المطورين",
    },
}


def generate_apple_pkpass(card_data: Dict[str, Any]) -> bytes:
    """
    Generates a cryptographically signed Apple Wallet .pkpass binary buffer
    using Python cryptography PKCS#7 detached signature.

    The pass face is a 'storeCard' whose strip carries the card art with the member's
    role and name drawn into it (see app/wallet_band.py). PassKit gives a pass no
    sharp full-card image before iOS 27 - background.png is eventTicket-only and
    always blurred - so the strip is the one place the card's own art and type can
    appear. iOS 27 reads the 'posterGeneric' block instead and draws the full-bleed
    artwork; older versions ignore it.
    """
    theme_id = card_data.get("themeId", DEFAULT_THEME)
    if theme_id not in THEMES_CONFIG:
        theme_id = DEFAULT_THEME
    theme = THEMES_CONFIG[theme_id]

    pass_type_id = config.APPLE_PASS_TYPE_ID
    team_id = config.APPLE_TEAM_ID
    p12_password_str = config.APPLE_P12_PASSWORD
    if not p12_password_str:
        raise ValueError("APPLE_P12_PASSWORD environment variable is not configured")
    p12_password = p12_password_str.encode("utf-8")

    uuid = card_data.get("uuid")
    serial_number = f"GDGQ-{uuid[:8].upper()}" if uuid else f"GDGQ-{str(int(time.time()))[-6:]}"
    qr_target_url = f"https://gdg-q.com/p/{uuid}" if uuid else "https://gdg-q.com"

    full_name = card_data.get("fullName") or "عضو GDG"
    uni_college = card_data.get("uniCollege") or card_data.get("institution") or "جامعة القصيم"
    major = card_data.get("major") or ""
    level = card_data.get("studyYearOrLevel") or ""

    role_title = theme["role_title"]
    name_field = {"key": "member_name", "label": role_title, "value": full_name}
    back_fields = [
        name_field,
        {"key": "uni_id", "label": "الرقم الجامعي", "value": str(card_data.get("uniId") or "")},
        {"key": "email", "label": "البريد الإلكتروني", "value": card_data.get("email", "")},
        {"key": "institution", "label": "الكلية / الجهة", "value": uni_college},
        {"key": "major", "label": "التخصص", "value": major},
        {"key": "level", "label": "المستوى / المرحلة", "value": level},
        {"key": "club_name", "label": "النادي", "value": "Google Developer Group - Qassim"},
    ]

    name_band = render_name_band(theme_id, role_title, full_name, theme["text_rgb"])
    if name_band is not None:
        # The name is already in the strip; any front field would be drawn on top of it.
        store_card: Dict[str, Any] = {"backFields": back_fields}
    else:
        # No baked text: show the name below the bare strip, where Wallet honours
        # foregroundColor. A primary field would sit on the strip in forced white.
        store_card = {
            "secondaryFields": [{**name_field, "textAlignment": "PKTextAlignmentRight"}],
            "backFields": back_fields[1:],
        }

    # 1. Build pass.json
    pass_json = {
        "formatVersion": 1,
        "passTypeIdentifier": pass_type_id,
        "teamIdentifier": team_id,
        "organizationName": "GDG Qassim",
        "serialNumber": serial_number,
        "description": role_title,
        "foregroundColor": theme["fg_rgb"],
        "backgroundColor": theme["bg_rgb"],
        "labelColor": theme["label_rgb"],
        "storeCard": store_card,
        "posterGeneric": {
            # A first primary field with no label is drawn as the poster's title.
            "primaryFields": [{"key": "member_name", "value": full_name}, {"key": "role", "value": role_title}],
            "backFields": back_fields[1:],
        },
        "barcodes": [
            {
                "format": "PKBarcodeFormatQR",
                "message": qr_target_url,
                "messageEncoding": "iso-8859-1",
                "altText": serial_number,
            }
        ],
        "barcode": {
            "format": "PKBarcodeFormatQR",
            "message": qr_target_url,
            "messageEncoding": "iso-8859-1",
            "altText": serial_number,
        },
    }

    files_to_pack: Dict[str, bytes] = {}
    files_to_pack["pass.json"] = json.dumps(pass_json, ensure_ascii=False, indent=2).encode("utf-8")

    # Pack the static images, preferring a theme's own variant (the admin card's
    # logo has a white wordmark for its black surface). `strip` is the bare band,
    # replaced below when the name could be drawn into it.
    for dest, source in [
        ("icon", "icon"),
        ("logo", "logo"),
        ("primaryLogo", "primaryLogo"),
        ("artwork", "artwork"),
        ("strip", "band"),
    ]:
        for suffix in ["", "@2x", "@3x"]:
            themed_path = os.path.join(ASSETS_DIR, f"{source}-{theme_id}{suffix}.png")
            default_path = os.path.join(ASSETS_DIR, f"{source}{suffix}.png")
            img_path = themed_path if os.path.exists(themed_path) else default_path
            if os.path.exists(img_path):
                with open(img_path, "rb") as f:
                    files_to_pack[f"{dest}{suffix}.png"] = f.read()

    for suffix, image in (name_band or {}).items():
        files_to_pack[f"strip{suffix}.png"] = image

    # Generate fallback transparent images if any missing
    for required in ["icon.png", "icon@2x.png", "logo.png", "logo@2x.png"]:
        if required not in files_to_pack:
            files_to_pack[required] = TRANSPARENT_PNG

    # 2. Build manifest.json
    manifest: Dict[str, str] = {}
    for filename, content in files_to_pack.items():
        manifest[filename] = hashlib.sha1(content).hexdigest()

    manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")
    files_to_pack["manifest.json"] = manifest_bytes

    # 3. Load Apple Developer Signing Certificate and Private Key
    p12_base64 = config.APPLE_P12_BASE64
    p12_path = config.APPLE_P12_PATH or os.path.join(os.path.dirname(__file__), "certificates", "Certificates.p12")

    p12_bytes = None
    if p12_base64:
        p12_bytes = base64.b64decode(p12_base64)
    elif os.path.exists(p12_path):
        with open(p12_path, "rb") as f:
            p12_bytes = f.read()

    if not p12_bytes:
        raise ValueError("Apple Pass signing certificate (APPLE_P12_BASE64 or Certificates.p12) is missing")

    private_key, certificate, additional_certs = pkcs12.load_key_and_certificates(p12_bytes, p12_password)
    if private_key is None or certificate is None:
        raise ValueError("Apple Pass P12 bundle is missing a private key or signing certificate")
    if not isinstance(private_key, (rsa.RSAPrivateKey, ec.EllipticCurvePrivateKey)):
        # PKCS7SignatureBuilder.add_signer only accepts these two key types; Apple
        # issues RSA certificates for pass signing, so anything else means a
        # bundle that was never meant for this purpose.
        raise ValueError(f"Apple Pass P12 private key must be RSA or EC, got {type(private_key).__name__}")

    wwdr_base64 = config.APPLE_WWDR_BASE64
    wwdr_path = config.APPLE_WWDR_PATH or os.path.join(os.path.dirname(__file__), "certificates", "AppleWWDRCAG4.cer")

    wwdr_cert = None
    if wwdr_base64:
        wwdr_bytes = base64.b64decode(wwdr_base64)
        try:
            wwdr_cert = x509.load_der_x509_certificate(wwdr_bytes)
        except Exception:
            wwdr_cert = x509.load_pem_x509_certificate(wwdr_bytes)
    elif os.path.exists(wwdr_path):
        with open(wwdr_path, "rb") as f:
            wwdr_bytes = f.read()
            try:
                wwdr_cert = x509.load_der_x509_certificate(wwdr_bytes)
            except Exception:
                wwdr_cert = x509.load_pem_x509_certificate(wwdr_bytes)

    if not wwdr_cert:
        raise ValueError("Apple WWDR Certificate (APPLE_WWDR_BASE64 or AppleWWDRCAG4.cer) is missing or invalid")

    # 4. Create PKCS#7 Detached Signature with SHA256
    builder = (
        PKCS7SignatureBuilder()
        .set_data(manifest_bytes)
        .add_signer(certificate, private_key, hashes.SHA256())
        .add_certificate(wwdr_cert)
    )
    if additional_certs:
        for extra_cert in additional_certs:
            builder = builder.add_certificate(extra_cert)

    signature_bytes = builder.sign(Encoding.DER, options=[PKCS7Options.DetachedSignature, PKCS7Options.Binary])
    files_to_pack["signature"] = signature_bytes

    # 5. Zip into in-memory .pkpass buffer
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for filename, content in files_to_pack.items():
            zip_file.writestr(filename, content)

    return zip_buffer.getvalue()


def generate_google_wallet_pass_url(card_data: Dict[str, Any]) -> str:
    """
    Generates a signed Google Wallet Save Link (JWT) using RS256 algorithm.
    heroImage is the wide strip banner, not the full card artwork: Google Wallet
    draws the barcode itself in a fixed spot mid-card, so the full artwork's own
    empty QR frame showed up as a second, blank square under the real code.
    """
    issuer_id = config.GOOGLE_WALLET_ISSUER_ID.strip()
    class_id = config.GOOGLE_WALLET_CLASS_ID.strip() or f"{issuer_id}.gdgq-card"
    service_account_email = config.GOOGLE_WALLET_SERVICE_ACCOUNT_EMAIL.strip()
    private_key_pem = config.GOOGLE_WALLET_PRIVATE_KEY

    if not issuer_id.isdigit():
        raise ValueError(
            "GOOGLE_WALLET_ISSUER_ID must be a numeric Google Wallet Issuer ID from the "
            "Google Wallet API console, not a Google Pay merchant ID."
        )
    if not service_account_email.endswith(".gserviceaccount.com"):
        raise ValueError("GOOGLE_WALLET_SERVICE_ACCOUNT_EMAIL is missing or invalid")
    if not private_key_pem.strip():
        raise ValueError("GOOGLE_WALLET_PRIVATE_KEY is not configured")

    theme_id = card_data.get("themeId", DEFAULT_THEME)
    theme = THEMES_CONFIG.get(theme_id, THEMES_CONFIG[DEFAULT_THEME])
    uuid = card_data.get("uuid")
    card_id = f"{issuer_id}.{uuid.replace('-', '_')}" if uuid else f"{issuer_id}.card_{int(time.time())}"
    qr_target_url = f"https://gdg-q.com/p/{uuid}" if uuid else "https://gdg-q.com"

    full_name = card_data.get("fullName") or "عضو GDG"
    email = card_data.get("email", "")
    uni_college = card_data.get("uniCollege") or card_data.get("institution") or "جامعة القصيم"
    major = card_data.get("major") or ""
    level = card_data.get("studyYearOrLevel") or ""

    generic_class = {"id": class_id}

    generic_object = {
        "id": card_id,
        "classId": class_id,
        "state": "ACTIVE",
        "cardTitle": {"defaultValue": {"language": "ar", "value": "Google Developer Groups - Qassim"}},
        "header": {"defaultValue": {"language": "ar", "value": full_name}},
        "subheader": {"defaultValue": {"language": "ar", "value": theme["role_title"]}},
        "hexBackgroundColor": theme["badge_color"],
        "logo": {
            "sourceUri": {"uri": "https://gdg-q.com/android-chrome-192x192.png"},
            "contentDescription": {"defaultValue": {"language": "ar", "value": "GDG Qassim Logo"}},
        },
        "heroImage": {
            "sourceUri": {"uri": f"https://gdg-q.com/wallet-v2/strip-{theme_id}@3x.png"},
            "contentDescription": {"defaultValue": {"language": "ar", "value": f"GDG Qassim {theme['role_title']}"}},
        },
        "textModulesData": [
            {"id": "uni_id", "header": "الرقم الجامعي", "body": str(card_data.get("uniId") or "عضو موثق")},
            {"id": "college", "header": "الجهة", "body": f"{uni_college}{(' · ' + major) if major else ''}"},
        ],
        "barcode": {"type": "QR_CODE", "value": qr_target_url, "alternateText": uuid[:8].upper() if uuid else "GDGQ"},
        "linksModuleData": {
            "uris": [{"uri": "https://gdg-q.com", "description": "مجتمع GDG Qassim", "id": "club_site"}]
        },
    }

    if level:
        generic_object["textModulesData"].append({"id": "level", "header": "المرحلة / المستوى", "body": level})

    if email:
        generic_object["textModulesData"].append({"id": "email", "header": "البريد الإلكتروني", "body": email})

    jwt_claims = {
        "iss": service_account_email,
        "aud": "google",
        "typ": "savetowallet",
        "iat": int(time.time()),
        "origins": ["https://gdg-q.com"],
        "payload": {"genericClasses": [generic_class], "genericObjects": [generic_object]},
    }

    # Format RSA private key
    formatted_key = private_key_pem.strip()
    if formatted_key.startswith('"') and formatted_key.endswith('"'):
        formatted_key = formatted_key[1:-1]
    formatted_key = formatted_key.replace("\\n", "\n")

    signed_jwt = jwt.encode(jwt_claims, formatted_key, algorithm="RS256")
    return f"https://pay.google.com/gp/v/save/{signed_jwt}"
