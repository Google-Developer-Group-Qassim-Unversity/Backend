from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response
from models import ValidateSheet
import pandas as pd
from pydantic import EmailStr, ValidationError, BaseModel
from datetime import datetime

router = APIRouter()

class EmailCheck(BaseModel):
    email: EmailStr

def validate_email(email: str) -> bool:
    try:
        EmailCheck(email=email)
        return True
    
    except ValidationError:
        return False

@router.post("/validate/sheet")
def validate_sheet(validation_sheet: ValidateSheet):
    EXPECTED_HEADERS = set(["name", "email", "uni id", "phone number", "gender"])

    try:
        df = pd.read_csv(validation_sheet.url_str)
        print("done")
    except Exception as e:
        print("not done", e)
        raise HTTPException(status_code=500, detail={
            "error": "Could not read the sheet.",
            "details": None
        })
    
    sheet_headers = set(df.columns.str.lower().str.strip())

    # check headers
    if sheet_headers != EXPECTED_HEADERS:
        missing_headers = [h for h in EXPECTED_HEADERS if h not in sheet_headers]

        if missing_headers:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={
                "error": "Missing columns",
                "details": missing_headers
            })
            
        
        extra_headers = [h.split(".")[0] for h in sheet_headers if h not in EXPECTED_HEADERS]
        if len(extra_headers) != (validation_sheet.end_date - validation_sheet.start_date).days + 1:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={
                "error": "Wrong number of columns.",
                "details": extra_headers
            })

        for header in extra_headers:
            s_header = header.strip().lower()
            if s_header[0:14] != "attendance day" or not s_header[14::].strip().isdigit():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={
                    "error": "Wrong column.",
                    "details": header
                })


        days = [int(day[14::].strip()) for day in extra_headers]
        
        sorted_days = sorted(days)

        if sorted_days[0] != 1:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={
                "error": "Attendance must begin with day 1.",
                "details": f"Current sheet attendance begin with day: {sorted_days[0]}"
            })

        if len(days) > 1:
            if not all(b - a for a, b in zip(days, days[1:])):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={
                    "error": "Attendace days have to be sequential.",
                    "details": f"Found {days}"
                })
            

    # check for missing names
    if df["name"].isnull().any():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={
            "error": "Missing names.",
            "details": f"Missing names on rows: {[idx+2 for idx in df.index[df['name'].isnull()]]}"
        })


    # check for missing uni ids
    if df["uni id"].isnull().any():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={
            "error": "Missing uni ids.",
            "detils": f"Missing uni ids on rows: {[idx+2 for idx in df.index[df['uni id'].isnull()]]}"
        })

    # check for duplicate uni ids
    dups = df[df["uni id"].duplicated(keep=False)].index.tolist()
    if dups:
        dups = [i+2 for i in dups]
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={
            "error": "Duplicates in uni id",
            "details": f"Uni id duplicates on rows {dups}"
        })


    # validate uni ids
    uni_ids = df.loc[:, "uni id"]
    fault_indecies = []

    # check if ids not digits
    for idx, value in uni_ids.items():
        value = str(value)

        if not value.isdigit():
            fault_indecies.append(idx + 2)

    if fault_indecies:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={
            "error": "Invalid uni ids.", 
            "details": f"uni ids are not numbers on rows: {fault_indecies}"
        })



    # check if ids not of length 9
    for idx, value in uni_ids.items():
        value = str(value)
  
        if len(value) != 9:
            fault_indecies.append(idx + 2)
        
    if fault_indecies:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={
            "error": "Length of uni ids must be 9.", 
            "details": f"Rows: {fault_indecies}"
        })


    #validate emails
    if df["email"].isnull().any():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={
            "error": "Missing emails.", 
            "details": f"Missing emails on rows: {[idx+2 for idx in df.index[df['email'].isnull()]]}"
        })
    
    emails = df.loc[:, "email"]
    fault_emails = []

    for idx, email in emails.items():
        if not validate_email(email):
            fault_emails.append(idx + 2)
    
    if fault_emails:
        print("invalid emails on rows", fault_emails)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={
            "error": "Invalid emails.",
            "details": f"Rows: {fault_emails}"
        })
    

    # validate gender
    if df["gender"].isnull().any():
        print("missing gender on rows", [idx+2 for idx in df.index[df["gender"].isnull()]])
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={
            "error": "Missing genders",
            "details": f"Missing genders on rows: {[idx+2 for idx in df.index[df['gender'].isnull()]]}"
        })
    
    genders = df.loc[:, "gender"]
    fault_genders = []

    for idx, gender in genders.items():
        if gender.lower() not in ["male", "female"]:
            fault_genders.append(idx+2)
    
    if fault_genders:
        print("invlid genders on rows: ", fault_genders)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={
            "error": "Invalid genders.", 
            "details": f"Genders must be either Male or Female on rows: {fault_emails} (We are not wok)"
        })
    
    return Response(status_code=status.HTTP_200_OK)