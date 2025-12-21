from fastapi.responses import StreamingResponse
from fastapi import APIRouter, status
from PIL import Image, ImageDraw, ImageFont
import qrcode
import os
import io
from app.routers.models import CardData
from pathlib import Path
router = APIRouter()
@router.post("/", status_code=status.HTTP_200_OK)
def get_card (data :CardData):
    BASE_DIR = Path(__file__).resolve().parent.parent  # go up to Backend/
    card_path = BASE_DIR / "assets" / "Jewel Background.jpeg"
    font_path = BASE_DIR / "assets" / "Tajawal-Regular.ttf"

    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    qr.add_data(data.url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white")
    qr_img = qr_img.resize((250, 250))

    # -------- Load card image --------
    card = Image.open(card_path).convert("RGBA")
    draw = ImageDraw.Draw(card)

    try:
        font = ImageFont.truetype(font_path, 70)
    except:
        font = ImageFont.load_default()

    # -------- Measure text using textbbox() --------
    bbox = draw.textbbox((0, 0), data.name, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    x = (card.width - text_width) // 2
    y = 50  # top margin

    draw.text((x, y), data.name, fill="black", font=font)

    # -------- Paste QR code --------
    qr_x = card.width - qr_img.width - 40
    qr_y = card.height - qr_img.height - 40
    card.paste(qr_img, (qr_x, qr_y))

    # -------- Convert final image to bytes to return --------
    img_bytes = io.BytesIO()
    card.save(img_bytes, format="PNG")
    img_bytes.seek(0)

    return StreamingResponse(img_bytes, media_type="image/png")



























