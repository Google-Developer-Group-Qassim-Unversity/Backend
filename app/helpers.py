from app.routers.models import Member_model
from typing import List, Union
from dotenv import load_dotenv
import pandas as pd
from pydantic import HttpUrl
import os
from json import dumps
from sys import exit

def get_pydantic_members(source: Union[str, HttpUrl]) -> List[tuple]:
    if isinstance(source, HttpUrl):
        print(f"[2] Got link: \x1b[33m{source}\x1b[0m")
        df = pd.read_csv(source.__str__())
    else:
        print(f"[2] Got file: \x1b[33m{source}\x1b[0m")
        df = pd.read_excel(f"uploads/{source}")
    
    members_and_date = []
    
    # Get column names
    df.columns = df.columns.str.strip()
    columns = df.columns.tolist()
    print(f"[2] Columns found: \x1b[36m{columns}\x1b[0m")
    print(f"[2] Date columns found: \x1b[36m{columns[5:]}\x1b[0m")

    # Iterate through DataFrame rows
    for _, row in df.iterrows():
        member = Member_model(
            name=row.get("name"),
            email=str(row.get("email")),
            phone_number=None if pd.isna(row.get("phone number")) or row.get("phone number") == "" else str(row.get("phone number")),
            uni_id=str(row.get("uni id")),
            gender=row.get("gender")
        )
        
        # Collecting all date columns (assuming first 5 columns are member data)
        date_columns = [row[col] for col in columns[5:]]
        
        members_and_date.append((member, date_columns))
    
    source_type = "URL" if isinstance(source, str) else "file"
    print(f"[2] Extracted \x1b[32m{len(members_and_date)}\x1b[0m members from {source_type}")
    return members_and_date

def get_uni_id_from_credentials(credentials):
    decoded = credentials.model_dump()['decoded']
    print("Got decoded credentials 🔒:")
    print(dumps(credentials.model_dump(), ensure_ascii=False, indent=4))
    uni_id = decoded['metadata']['uni_id']
    return uni_id

def credentials_to_member_model(credentials) -> Member_model:
    credentials_dict = credentials.model_dump()
    credentials_str = dumps(credentials.model_dump(), ensure_ascii=False, indent=4)
    if not credentials_dict['decoded']['metadata']:
        print(f"Invalid credentials structure:\n{credentials_str}")
        raise ValueError("Invalid credentials: 'decoded' or 'metadata' missing")
    print(f"got credentials:\n{credentials_str}")
    metadata = credentials_dict['decoded']['metadata']
    print(f"got metadata:\n{dumps(metadata, ensure_ascii=False, indent=4)}")
    member = Member_model(
        name=metadata.get('fullArabicName'),
        email=metadata.get('personalEmail'),
        phone_number=metadata.get('saudiPhone'),
        uni_id=metadata.get('uni_id'),
        gender=metadata.get("gender").title()
    )
    print(f"Converted to Member_model:\n{member.model_dump()}")
    return member

def get_database_url():
    '''
    This function is so that you use the fake DB while running localhost
    but when you deploy it, it  will automatically uses the actual DB
    Make sure to set the DEV_DATABASE_URL in your .env file locally @albrrak773
    '''
    load_dotenv()
    dev_url = os.getenv("DEV_DATABASE_URL")
    if dev_url is not None:
        return "DEV_DATABASE_URL"
    # If DEV_DATABASE_URL doesn't exist, return the string "DATABASE_URL"
    url = os.getenv("DATABASE_URL")
    if url is None or url == "":
        raise ValueError(f"\n⚠️ DATABASE_URL is not set in the environment variables.\n")
    else:
        return "DATABASE_URL" 
        
def is_clerk_dev():
    clerk_dev = os.getenv("CLERK_ENV")
    if clerk_dev:
        return True
    return False

if __name__ == "__main__":
    url = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTI-xnfTaaEhNO4G4Vx1dJejKq2kDtHSi5yWtcrFGNfKJJxqRvIpBXk2_M9dxDc49NrDY-dD5SiJ6pR/pub?gid=1781104695&single=true&output=csv"
    members = get_pydantic_members(url)
    print(f"done ✅ got {len(members)} members")