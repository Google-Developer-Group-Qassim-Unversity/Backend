from fastapi import APIRouter, UploadFile, File, HTTPException, Form, Depends
import os
import uuid
from datetime import datetime
from fastapi import APIRouter, HTTPException, UploadFile, Depends
import pandas as pd
from pydantic import EmailStr, ValidationError, BaseModel

router = APIRouter()

UPLOAD_DIR = "./uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

def parse_upload_members_form(
    start_date: datetime = Form(...),
    end_date: datetime = Form(...),
    file: UploadFile = File(...),
) -> tuple[datetime, datetime, UploadFile]:
    """
    Parse form data for /upload/members endpoint.
    Validates that file, start_date, and end_date are provided.
    """
    return start_date, end_date, file

async def _uploaded_file(file: UploadFile) -> tuple[str, str]:
	file_id = str(uuid.uuid4())
	extension = os.path.splitext(file.filename)[1]
	file_name = file_id + extension
	file_location = os.path.join(UPLOAD_DIR, file_name)

	try:
		with open(file_location, "wb") as f:
			content = await file.read()
			f.write(content)
	except Exception as e:
		raise HTTPException(status_code=500, detail=f"File upload failed: {str(e)}")
	
	return file_name, file_location

@router.post("/", status_code=201)
async def upload_file(file: UploadFile = File(...)):
	file_name, _ = await _uploaded_file(file)
	return {"file": file_name}

@router.post("/members", status_code=201)
async def upload_members(parsed: tuple = Depends(parse_upload_members_form)):
	start_date, end_date, file = parsed
	
	file_name, file_location = await _uploaded_file(file)

	try:
		validate_sheet(file_location, start_date, end_date)
	except ValueError as e:
		os.remove(file_location)
		raise HTTPException(status_code=401, detail=e.args[0])

	return {"file": file_name}

def validate_sheet(file_path: str, start_date, end_date):
	"""
	Validate a sheet file at file_path. Raises ValueError with details on failure.
	expected_headers: set of expected column names (lowercase, stripped)
	start_date, end_date: required for attendance day columns check (datetime.date)
	"""
	expected_headers = set(["name", "email", "uni id", "phone number", "gender", "Attendance day 1"])


	try:
		if file_path.lower().endswith(".xlsx") or file_path.lower().endswith(".xls"):
			df = pd.read_excel(file_path)
		else:
			df = pd.read_csv(file_path)
	except Exception as e:
		raise ValueError({
			"error": "Could not read the sheet.",
			"details": str(e)
		})

	sheet_headers = set(df.columns.str.strip())

	# check headers
	if sheet_headers != expected_headers:
		missing_headers = [h for h in expected_headers if h not in sheet_headers]
		if missing_headers:
			raise ValueError({
				"error": "Missing columns",
				"details": missing_headers
			})

		extra_headers = [h.split(".")[0] for h in sheet_headers if h not in expected_headers]
		if start_date is not None and end_date is not None:
			if len(extra_headers) != (end_date - start_date).days + 1:
				raise ValueError({
					"error": f"Wrong number of columns, should be {len(expected_headers) + ((end_date - start_date).days)} columns not {len(expected_headers) + len(extra_headers)}.",
					"details": "extra columns: " + ", ".join(extra_headers)
				})

		for header in extra_headers:
			s_header = header.strip().lower()
			if s_header[0:14] != "attendance day" or not s_header[14::].strip().isdigit():
				raise ValueError({
					"error": "Wrong column.",
					"details": header
				})

		days = [int(day[14::].strip()) for day in extra_headers]
		sorted_days = sorted(days)
		if sorted_days and sorted_days[0] != 1:
			raise ValueError({
				"error": "Attendance must begin with day 1.",
				"details": f"Current sheet attendance begin with day: {sorted_days[0]}"
			})
		if len(days) > 1:
			if not all(b - a for a, b in zip(days, days[1:])):
				raise ValueError({
					"error": "Attendace days have to be sequential.",
					"details": f"Found {days}"
				})

	# check for missing names
	if df["name"].isnull().any():
		raise ValueError({
			"error": "Missing names.",
			"details": f"Missing names on rows: {[idx+2 for idx in df.index[df['name'].isnull()]]}"
		})

	# check for missing uni ids
	if df["uni id"].isnull().any():
		raise ValueError({
			"error": "Missing uni ids.",
			"details": f"Missing uni ids on rows: {[idx+2 for idx in df.index[df['uni id'].isnull()]]}"
		})

	# check for duplicate uni ids
	dups = df[df["uni id"].duplicated(keep=False)].index.tolist()
	if dups:
		dups = [i+2 for i in dups]
		raise ValueError({
			"error": "Duplicates in uni id",
			"details": f"Uni id duplicates on rows {dups}"
		})

	# validate uni ids
	uni_ids = df.loc[:, "uni id"]
	fault_indecies = []
	for idx, value in uni_ids.items():
		value = str(value)
		if not value.isdigit():
			fault_indecies.append(idx + 2)
	if fault_indecies:
		raise ValueError({
			"error": "Invalid uni ids.", 
			"details": f"uni ids are not numbers on rows: {fault_indecies}"
		})

	# check if ids not of length 9
	fault_length = []
	for idx, value in uni_ids.items():
		value = str(value)
		if len(value) != 9:
			fault_length.append(idx + 2)
	if fault_length:
		raise ValueError({
			"error": "Length of uni ids must be 9.", 
			"details": f"Rows: {fault_length}"
		})

	#validate emails
	if df["email"].isnull().any():
		raise ValueError({
			"error": "Missing emails.", 
			"details": f"Missing emails on rows: {[idx+2 for idx in df.index[df['email'].isnull()]]}"
		})
	emails = df.loc[:, "email"]
	fault_emails = []
	for idx, email in emails.items():
		if not validate_email(email):
			fault_emails.append(idx + 2)
	if fault_emails:
		raise ValueError({
			"error": "Invalid emails.",
			"details": f"Rows: {fault_emails}"
		})

	# validate gender
	if df["gender"].isnull().any():
		raise ValueError({
			"error": "Missing genders",
			"details": f"Missing genders on rows: {[idx+2 for idx in df.index[df['gender'].isnull()]]}"
		})
	genders = df.loc[:, "gender"]
	fault_genders = []
	for idx, gender in genders.items():
		if gender.lower() not in ["male", "female"]:
			fault_genders.append(idx+2)
	if fault_genders:
		raise ValueError({
			"error": "Invalid genders.", 
			"details": f"Genders must be either Male or Female on rows: {fault_genders}"
		})
	return True

class EmailCheck(BaseModel):
    email: EmailStr

def validate_email(email: str) -> bool:
    try:
        EmailCheck(email=email)
        return True
    
    except ValidationError:
        return False