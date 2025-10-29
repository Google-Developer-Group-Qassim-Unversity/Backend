from fastapi import APIRouter, UploadFile, File, HTTPException
import os
import uuid

router = APIRouter()

# Ensure /uploads directory exists in project root
UPLOAD_DIR = "./uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

@router.post("/upload/members", status_code=201)
async def upload_file(file: UploadFile = File(...)):
	file_id = str(uuid.uuid4())
	extension = os.path.splitext(file.filename)[1]
	file_name_with_ext = file_id + extension
	file_location = os.path.join(UPLOAD_DIR, file_name_with_ext)
	try:
		with open(file_location, "wb") as f:
			content = await file.read()
			f.write(content)
	except Exception as e:
		raise HTTPException(status_code=500, detail=f"File upload failed: {str(e)}")
	return {"ID": file_id}



