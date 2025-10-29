import urllib.request
import csv
import io
from DB.models import Member
from typing import List
from dotenv import load_dotenv
from typing import Union, Tuple
from pathlib import Path
import pandas as pd
from fastapi import UploadFile

import os

def get_pydantic_members(source: Union[UploadFile, str]) -> List[tuple]:
    if isinstance(source, str):
        # Handle URL
        print(f"Got link: \x1b[33m{source}\x1b[0m")
        df = pd.read_csv(source)
    else:
        # Handle UploadFile
        print(f"Got file: \x1b[33m{source.filename}\x1b[0m")
        df = pd.read_excel(source.file)
    
    members_and_date = []
    
    # Get column names
    columns = df.columns.tolist()
    print(f"Columns found: \x1b[36m{columns}\x1b[0m")
    print(f"Date columns found: \x1b[36m{columns[5:]}\x1b[0m")
    
    # Iterate through DataFrame rows
    for _, row in df.iterrows():
        member = Member(
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
    print(f"Extracted \x1b[32m{len(members_and_date)}\x1b[0m members from {source_type}")
    return members_and_date

def get_database_url():
    ''''
    This function is so that you use the fake DB while running localhost
    but when you deploy it, it  will automatically uses the actual DB
    Make sure to set the DEV_DATABASE_URL in your .env file locally @albrrak773
    '''
    load_dotenv()
    dev_url = os.getenv("DEV_DATABASE_URL")
    if dev_url is not None:
        return "DEV_DATABASE_URL"
    # If DEV_DATABASE_URL doesn't exist, return the string "DATABASE_URL"
    return "DATABASE_URL"


if __name__ == "__main__":
    url = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTI-xnfTaaEhNO4G4Vx1dJejKq2kDtHSi5yWtcrFGNfKJJxqRvIpBXk2_M9dxDc49NrDY-dD5SiJ6pR/pub?gid=1781104695&single=true&output=csv"
    members = get_pydantic_members(url)
    print(f"done ✅ got {len(members)} members")