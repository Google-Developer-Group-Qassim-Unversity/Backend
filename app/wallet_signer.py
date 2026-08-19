import base64
import hashlib
import io
import json
import os
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional

import jwt
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import pkcs12, Encoding
from cryptography.hazmat.primitives.serialization.pkcs7 import PKCS7SignatureBuilder, PKCS7Options
from cryptography.x509 import load_der_x509_certificate, load_pem_x509_certificate


THEMES_CONFIG = {
    "gdg-blue": {
        "role_title": "عضو نادي قوقل للطلبة المطورين",
        "is_admin": False,
        "bg_hex": "#ffffff",
        "badge_color": "#2563eb",
        "bg_rgb": "rgb(255, 255, 255)",
        "fg_rgb": "rgb(151, 151, 151)",
        "label_rgb": "rgb(0, 0, 0)",
    },
    "gdg-red": {
        "role_title": "عضو نادي قوقل للطلبة المطورين",
        "is_admin": False,
        "bg_hex": "#ffffff",
        "badge_color": "#e11d48",
        "bg_rgb": "rgb(255, 255, 255)",
        "fg_rgb": "rgb(151, 151, 151)",
        "label_rgb": "rgb(0, 0, 0)",
    },
    "gdg-gold-admin": {
        "role_title": "إداري نادي قوقل للطلبة المطورين",
        "is_admin": True,
        "bg_hex": "#ffffff",
        "badge_color": "#f59e0b",
        "bg_rgb": "rgb(255, 255, 255)",
        "fg_rgb": "rgb(151, 151, 151)",
        "label_rgb": "rgb(0, 0, 0)",
    },
}

DEFAULT_THEME = "gdg-blue"


def generate_apple_pkpass(card_data: Dict[str, Any]) -> bytes:
    """
    Generates a cryptographically signed Apple Wallet .pkpass binary buffer
    using Python cryptography PKCS#7 detached signature.
    """
    theme_id = card_data.get("themeId", DEFAULT_THEME)
    theme = THEMES_CONFIG.get(theme_id, THEMES_CONFIG[DEFAULT_THEME])

    pass_type_id = os.getenv("APPLE_PASS_TYPE_ID", "pass.pass.com.gdg-q.wallet")
    team_id = os.getenv("APPLE_TEAM_ID", "7NN7W24VXR")
    p12_password_str = os.getenv("APPLE_P12_PASSWORD")
    if not p12_password_str:
        raise ValueError("APPLE_P12_PASSWORD environment variable is not configured")
    p12_password = p12_password_str.encode("utf-8")

    uuid = card_data.get("uuid")
    serial_number = f"GDGQ-{uuid[:8].upper()}" if uuid else f"GDGQ-{str(int(time.time()))[-6:]}"
    qr_target_url = f"https://gdg-q.com/wallet/{uuid}" if uuid else "https://gdg-q.com"

    full_name = card_data.get("fullName") or "عضو GDG"
    english_name = card_data.get("englishName") or full_name

    # 1. Build pass.json
    pass_json = {
        "formatVersion": 1,
        "passTypeIdentifier": pass_type_id,
        "teamIdentifier": team_id,
        "organizationName": "GDG Qassim",
        "serialNumber": serial_number,
        "description": theme["role_title"],
        "foregroundColor": theme["fg_rgb"],
        "backgroundColor": theme["bg_rgb"],
        "labelColor": theme["label_rgb"],
        # Store cards are the Apple Wallet layout that supports strip.png on
        # current iOS releases. A generic pass ignores the strip artwork, which
        # left members with a flat colour card instead of the Figma ribbon.
        "storeCard": {
            "secondaryFields": [
                {"key": "english-name", "label": "Name", "value": english_name},
                {"key": "arabic-name", "label": "الاسم", "value": full_name},
            ],
        },
        "barcodes": [
            {
                "format": "PKBarcodeFormatQR",
                "message": qr_target_url,
                "messageEncoding": "iso-8859-1",
                "altText": serial_number,
            }
        ],
    }

    pass_json_bytes = json.dumps(pass_json, ensure_ascii=False, indent=2).encode("utf-8")

    # 2. Collect files and calculate sha1 manifest
    assets_dir = Path(__file__).parent / "assets" / "gdg.pass"
    files_to_pack: Dict[str, bytes] = {"pass.json": pass_json_bytes}

    # Base Icons and Logos
    for img_name in [
        "icon.png",
        "icon@2x.png",
        "icon@3x.png",
        "logo.png",
        "logo@2x.png",
        "logo@3x.png",
    ]:
        img_path = assets_dir / img_name
        if img_path.exists():
            files_to_pack[img_name] = img_path.read_bytes()

    # Dynamic Theme Strip Artwork (Figma waves and medal)
    theme_strip_map = {
        "strip.png": assets_dir / f"strip-{theme_id}.png",
        "strip@2x.png": assets_dir / f"strip-{theme_id}@2x.png",
        "strip@3x.png": assets_dir / f"strip-{theme_id}@3x.png",
    }
    for target_name, theme_path in theme_strip_map.items():
        if theme_path.exists():
            files_to_pack[target_name] = theme_path.read_bytes()
        elif (assets_dir / target_name).exists():
            files_to_pack[target_name] = (assets_dir / target_name).read_bytes()

    manifest: Dict[str, str] = {}
    for filename, content in files_to_pack.items():
        manifest[filename] = hashlib.sha1(content).hexdigest()

    manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")
    files_to_pack["manifest.json"] = manifest_bytes

    # 3. Load certificates from environment or local certificates folder
    p12_base64 = os.getenv("APPLE_P12_BASE64")
    wwdr_base64 = os.getenv("APPLE_WWDR_BASE64")

    p12_bytes = None
    if p12_base64:
        p12_bytes = base64.b64decode(p12_base64)
    else:
        certs_dir = Path(__file__).parent.parent / "certificates"
        local_p12 = certs_dir / "apple-wallet-pass-certificate.p12"
        if local_p12.exists():
            p12_bytes = local_p12.read_bytes()

    if not p12_bytes:
        raise ValueError("Apple Wallet certificate (.p12) is not configured in environment or certificates/ folder")

    private_key, certificate, additional_certs = pkcs12.load_key_and_certificates(p12_bytes, p12_password)

    if not private_key or not certificate:
        raise ValueError("Invalid Apple PKCS#12 certificate or private key")

    wwdr_cert = None
    if wwdr_base64:
        wwdr_bytes = base64.b64decode(wwdr_base64)
        try:
            wwdr_cert = load_der_x509_certificate(wwdr_bytes)
        except Exception:
            wwdr_cert = load_pem_x509_certificate(wwdr_bytes)
    else:
        certs_dir = Path(__file__).parent.parent / "certificates"
        local_wwdr = certs_dir / "AppleWWDRCAG4.cer"
        if local_wwdr.exists():
            wwdr_cert = load_der_x509_certificate(local_wwdr.read_bytes())

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
    """
    issuer_id = os.getenv("GOOGLE_WALLET_ISSUER_ID", "BCR2DN6DTK643EAC")
    class_id = os.getenv("GOOGLE_WALLET_CLASS_ID", f"{issuer_id}.gdgq-card")
    service_account_email = os.getenv("GOOGLE_WALLET_SERVICE_ACCOUNT_EMAIL", "gdgq-962@gdgcoc.iam.gserviceaccount.com")
    private_key_pem = os.getenv("GOOGLE_WALLET_PRIVATE_KEY", "")

    theme_id = card_data.get("themeId", DEFAULT_THEME)
    theme = THEMES_CONFIG.get(theme_id, THEMES_CONFIG[DEFAULT_THEME])
    uuid = card_data.get("uuid")
    card_id = f"{issuer_id}.{uuid.replace('-', '_')}" if uuid else f"{issuer_id}.card_{int(time.time())}"
    qr_target_url = f"https://gdg-q.com/wallet/{uuid}" if uuid else "https://gdg-q.com"

    major = card_data.get("major") or (
        "المرحلة الثانوية" if card_data.get("educationLevel") == "highschool" else "علوم حاسب"
    )
    institution = card_data.get("institution") or (
        "مدرسة ثانوية" if card_data.get("educationLevel") == "highschool" else "جامعة القصيم"
    )
    level = card_data.get("studyYearOrLevel") or (
        "خريج معتمد" if card_data.get("userStatus") == "graduate" else "عضو مجتمع GDG"
    )
    full_name = card_data.get("fullName") or "عضو GDG"
    phone = card_data.get("phone", "")
    country_code = card_data.get("countryCode", "+966")
    email = card_data.get("email", "")

    generic_object = {
        "id": card_id,
        "classId": class_id,
        "cardTitle": {"defaultValue": {"language": "ar", "value": "GDG QASSIM"}},
        "header": {"defaultValue": {"language": "ar", "value": full_name}},
        "subheader": {"defaultValue": {"language": "ar", "value": major}},
        "hexBackgroundColor": theme["badge_color"],
        "logo": {
            "sourceUri": {"uri": "https://gdg-q.com/logo.png"},
            "contentDescription": {"defaultValue": {"language": "ar", "value": "GDG Qassim Logo"}},
        },
        "textModulesData": [
            {"id": "institution", "header": "الصرح التعليمي", "body": institution},
            {"id": "level", "header": "المستوى / المرحلة", "body": level},
        ],
        "barcode": {"type": "QR_CODE", "value": qr_target_url, "alternateText": uuid[:8].upper() if uuid else "GDGQ"},
    }

    if phone:
        generic_object["textModulesData"].append({"id": "phone", "header": "الجوال", "body": f"{country_code} {phone}"})

    if email:
        generic_object["textModulesData"].append({"id": "email", "header": "البريد الإلكتروني", "body": email})

    jwt_claims = {
        "iss": service_account_email,
        "aud": "google",
        "typ": "savetoandroidpay",
        "iat": int(time.time()),
        "origins": ["https://gdg-q.com", "http://localhost:3000"],
        "payload": {"genericObjects": [generic_object]},
    }

    if not private_key_pem:
        return f"https://pay.google.com/gp/v/save/{uuid or 'demo'}"

    # Format key
    formatted_key = private_key_pem.strip()
    if formatted_key.startswith('"') and formatted_key.endswith('"'):
        formatted_key = formatted_key[1:-1]
    formatted_key = formatted_key.replace("\\n", "\n")

    signed_jwt = jwt.encode(jwt_claims, formatted_key, algorithm="RS256")
    return f"https://pay.google.com/gp/v/save/{signed_jwt}"
