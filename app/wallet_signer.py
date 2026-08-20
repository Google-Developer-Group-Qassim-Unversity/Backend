import base64
import hashlib
import io
import json
import logging
import os
import time
import zipfile
from typing import Any, Dict, List, Optional
from PIL import Image

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs12, pkcs7, Encoding
from cryptography.hazmat.primitives.serialization.pkcs7 import PKCS7Options, PKCS7SignatureBuilder

logger = logging.getLogger(__name__)

# Base path for static wallet assets
ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets", "wallet")

DEFAULT_THEME = "gdg-blue"
THEMES_CONFIG = {
    "gdg-blue": {
        "bg_rgb": "rgb(24, 60, 150)",
        "fg_rgb": "rgb(255, 255, 255)",
        "label_rgb": "rgb(200, 220, 255)",
        "badge_color": "#1A73E8",
        "role_title": "عضو مجتمع GDG",
    },
    "gdg-red": {
        "bg_rgb": "rgb(180, 40, 40)",
        "fg_rgb": "rgb(255, 255, 255)",
        "label_rgb": "rgb(255, 210, 210)",
        "badge_color": "#EA4335",
        "role_title": "عضو مميز",
    },
    "gdg-gold-admin": {
        "bg_rgb": "rgb(40, 35, 20)",
        "fg_rgb": "rgb(255, 225, 120)",
        "label_rgb": "rgb(218, 165, 32)",
        "badge_color": "#D4AF37",
        "role_title": "إدارة GDG Qassim",
    },
}


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
    qr_target_url = f"https://gdg-q.com/p/{uuid}" if uuid else "https://gdg-q.com"

    full_name = card_data.get("fullName") or "عضو GDG"

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
        "storeCard": {
            "secondaryFields": [
                {
                    "key": "member_name",
                    "label": theme["role_title"],
                    "value": full_name,
                },
            ],
            "backFields": [
                {
                    "key": "uni_id",
                    "label": "الرقم الجامعي",
                    "value": str(card_data.get("uniId") or ""),
                },
                {
                    "key": "email",
                    "label": "البريد الإلكتروني",
                    "value": card_data.get("email", ""),
                },
                {
                    "key": "institution",
                    "label": "الكلية / الجهة",
                    "value": card_data.get("uniCollege", "جامعة القصيم"),
                },
                {
                    "key": "major",
                    "label": "التخصص",
                    "value": card_data.get("major", ""),
                },
                {
                    "key": "level",
                    "label": "المستوى / المرحلة",
                    "value": card_data.get("studyYearOrLevel", ""),
                },
                {
                    "key": "public_profile",
                    "label": "رابط الصفحة الشخصية المعتمدة",
                    "value": qr_target_url,
                },
                {
                    "key": "club_name",
                    "label": "النادي",
                    "value": "Google Developer Group on Campus - Qassim University",
                },
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
        "barcode": {
            "format": "PKBarcodeFormatQR",
            "message": qr_target_url,
            "messageEncoding": "iso-8859-1",
            "altText": serial_number,
        },
    }

    files_to_pack: Dict[str, bytes] = {}
    files_to_pack["pass.json"] = json.dumps(pass_json, ensure_ascii=False, indent=2).encode("utf-8")

    # Load high-res images for strip, logo, and icon
    for img_name in ["icon.png", "icon@2x.png", "logo.png", "logo@2x.png", "strip.png", "strip@2x.png"]:
        theme_img_path = os.path.join(ASSETS_DIR, f"{theme_id}_{img_name}")
        default_img_path = os.path.join(ASSETS_DIR, img_name)
        if os.path.exists(theme_img_path):
            with open(theme_img_path, "rb") as f:
                files_to_pack[img_name] = f.read()
        elif os.path.exists(default_img_path):
            with open(default_img_path, "rb") as f:
                files_to_pack[img_name] = f.read()

    # Generate fallback transparent images if any missing
    for required in ["icon.png", "icon@2x.png", "logo.png", "logo@2x.png"]:
        if required not in files_to_pack:
            img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            files_to_pack[required] = buf.getvalue()

    # 2. Build manifest.json
    manifest: Dict[str, str] = {}
    for filename, content in files_to_pack.items():
        manifest[filename] = hashlib.sha1(content).hexdigest()

    manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")
    files_to_pack["manifest.json"] = manifest_bytes

    # 3. Load Apple Developer Signing Certificate and Private Key
    p12_base64 = os.getenv("APPLE_P12_BASE64")
    p12_path = os.getenv("APPLE_P12_PATH", os.path.join(os.path.dirname(__file__), "certificates", "Certificates.p12"))

    p12_bytes = None
    if p12_base64:
        p12_bytes = base64.b64decode(p12_base64)
    elif os.path.exists(p12_path):
        with open(p12_path, "rb") as f:
            p12_bytes = f.read()

    if not p12_bytes:
        raise ValueError("Apple Pass signing certificate (APPLE_P12_BASE64 or Certificates.p12) is missing")

    private_key, certificate, additional_certs = pkcs12.load_key_and_certificates(
        p12_bytes,
        p12_password,
    )

    wwdr_base64 = os.getenv("APPLE_WWDR_BASE64")
    wwdr_path = os.getenv("APPLE_WWDR_PATH", os.path.join(os.path.dirname(__file__), "certificates", "AppleWWDRCAG4.cer"))

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
    Includes both genericClass and genericObject definitions in the JWT payload
    to guarantee on-the-fly class creation without pre-existing API registration.
    """
    issuer_id = os.getenv("GOOGLE_WALLET_ISSUER_ID", "BCR2DN6DTK643EAC")
    class_id = os.getenv("GOOGLE_WALLET_CLASS_ID", f"{issuer_id}.gdgq-card")
    service_account_email = os.getenv("GOOGLE_WALLET_SERVICE_ACCOUNT_EMAIL", "gdgq-962@gdgcoc.iam.gserviceaccount.com")
    private_key_pem = os.getenv("GOOGLE_WALLET_PRIVATE_KEY", "")

    theme_id = card_data.get("themeId", DEFAULT_THEME)
    theme = THEMES_CONFIG.get(theme_id, THEMES_CONFIG[DEFAULT_THEME])
    uuid = card_data.get("uuid")
    card_id = f"{issuer_id}.{uuid.replace('-', '_')}" if uuid else f"{issuer_id}.card_{int(time.time())}"
    qr_target_url = f"https://gdg-q.com/p/{uuid}" if uuid else "https://gdg-q.com"

    full_name = card_data.get("fullName") or "عضو GDG"
    email = card_data.get("email", "")

    # Define GenericClass inside JWT for self-contained pass creation
    generic_class = {
        "id": class_id,
        "classTemplateInfo": {
            "cardTemplateOverride": {
                "cardRowTemplateInfos": [
                    {
                        "twoItems": {
                            "startItem": {
                                "firstValue": {
                                    "fields": [
                                        {"fieldPath": "object.textModulesData['uni_id']"}
                                    ]
                                }
                            },
                            "endItem": {
                                "firstValue": {
                                    "fields": [
                                        {"fieldPath": "object.textModulesData['college']"}
                                    ]
                                }
                            },
                        }
                    }
                ]
            }
        },
    }

    generic_object = {
        "id": card_id,
        "classId": class_id,
        "cardTitle": {
            "defaultValue": {
                "language": "ar",
                "value": "GDG QASSIM",
            }
        },
        "header": {
            "defaultValue": {
                "language": "ar",
                "value": full_name,
            }
        },
        "subheader": {
            "defaultValue": {
                "language": "ar",
                "value": theme["role_title"],
            }
        },
        "hexBackgroundColor": theme["badge_color"],
        "logo": {
            "sourceUri": {
                "uri": "https://gdg-q.com/logo.png",
            },
            "contentDescription": {
                "defaultValue": {
                    "language": "ar",
                    "value": "GDG Qassim Logo",
                }
            },
        },
        "textModulesData": [
            {
                "id": "uni_id",
                "header": "الرقم الجامعي",
                "body": str(card_data.get("uniId") or ""),
            },
            {
                "id": "college",
                "header": "الكلية",
                "body": card_data.get("uniCollege") or "جامعة القصيم",
            },
        ],
        "barcode": {
            "type": "QR_CODE",
            "value": qr_target_url,
            "alternateText": uuid[:8].upper() if uuid else "GDGQ",
        },
    }

    if email:
        generic_object["textModulesData"].append({
            "id": "email",
            "header": "البريد الإلكتروني",
            "body": email,
        })

    jwt_claims = {
        "iss": service_account_email,
        "aud": "google",
        "typ": "savetoandroidpay",
        "iat": int(time.time()),
        "payload": {
            "genericClasses": [generic_class],
            "genericObjects": [generic_object],
        },
    }

    if not private_key_pem:
        return f"https://pay.google.com/gp/v/save/{uuid or 'demo'}"

    # Format RSA private key
    formatted_key = private_key_pem.strip()
    if formatted_key.startswith('"') and formatted_key.endswith('"'):
        formatted_key = formatted_key[1:-1]
    formatted_key = formatted_key.replace("\\n", "\n")

    signed_jwt = jwt.encode(jwt_claims, formatted_key, algorithm="RS256")
    return f"https://pay.google.com/gp/v/save/{signed_jwt}"
