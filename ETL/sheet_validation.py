from fastapi import APIRouter
from models import ValidateSheet
import pandas as pd
from pydantic import EmailStr, ValidationError, BaseModel
from datetime import date

#router = APIRouter()

class EmailCheck(BaseModel):
    email: EmailStr

def validate_email(email: str) -> bool:
    try:
        EmailCheck(email=email)
        return True
    
    except ValidationError:
        return False


#@router.post("/validate/sheet")
sheet_url = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRcmCPGpWxuj9y8LEjeKPhBW77qRvyWs3g5wAH5eA5weEPASXj-FvhLUwa_CNW5ZX9D6c3qyOk5bej0/pub?gid=1781104695&single=true&output=csv"

validate_sheet = ValidateSheet(
    url=sheet_url, 
    start_date= date(2025, 9, 20),
    end_date=date(2025, 9, 20)
)


def validate_sheet(validate_sheet: ValidateSheet):
    EXPECTED_HEADERS = set(["name", "email", "uni id", "phone number", "gender"])

    try:
        df = pd.read_csv("test.csv")
    except Exception as e:
        print("error could not open sheet", e)
        return
    
    sheet_headers = set(df.columns.str.lower().str.strip())

    # check headers
    if sheet_headers != EXPECTED_HEADERS:
        missing_headers = [h for h in EXPECTED_HEADERS if h not in sheet_headers]
        extra_headers = [h for h in sheet_headers if h not in EXPECTED_HEADERS]

        if missing_headers:
            print("missing headers", missing_headers)
            return missing_headers
        else:
            print("extra headers", extra_headers)
            return extra_headers
        
    # check for missin names
    if df.name.isnull().any():
        print("missing emails on rows:", [idx+2 for idx in df.index[df["email"].isnull()]])       
        return 

    # check for missing uni ids
    if df["uni id"].isnull().any():
        print("missing uni id on row: ", [idx+2 for idx in df.index[df["uni id"].isnull()]])
        return

    # validate uni ids
    uni_ids = df.loc[:, "uni id"]
    fault_indecies = []

    # check if ids not digits
    for idx, value in uni_ids.items():
        value = str(value)
        if not value.isdigit():
            fault_indecies.append(idx + 2)

    if fault_indecies:
        print("wrong ids", fault_indecies)
        return fault_indecies


    # check if ids not of length 9
    for idx, value in uni_ids.items():
        value = str(value)
  
        if len(value) != 9:
            fault_indecies.append(idx + 2)
        
    if fault_indecies:
        print("wrong length of id", fault_indecies)
        return fault_indecies


    #validate emails
    if df["email"].isnull().any():
        print("missing emails on rows: ", [idx+2 for idx in df.index[df["email"].isnull()]])
        return
    
    emails = df.loc[:, "email"]
    fault_emails = []

    for idx, email in emails.items():
        if not validate_email(email):
            fault_emails.append(idx + 2)
    
    if fault_emails:
        print("invalid emails on rows", fault_emails)
        return fault_emails
    
    # validate gender
    if df["gender"].isnull().any():
        print("missing gender on rows", [idx+2 for idx in df.index[df["gender"].isnull()]])
        return
    
    genders = df.loc[:, "gender"]
    fault_genders = []

    for idx, gender in genders.items():
        if gender.lower() not in ["male", "female"]:
            fault_genders.append(idx+2)
    
    if fault_genders:
        print("invlid genders on rows: ", fault_genders)
        return fault_genders
    
    print("perfect")
validate_sheet(validate_sheet=validate_sheet)