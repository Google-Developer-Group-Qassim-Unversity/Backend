import urllib.request
import csv
import io
from models import Member
from typing import List
from dotenv import load_dotenv
import os
def csv_to_pydantic_member(url) -> List[Member]:
    print(f"Got link: \x1b[33m{url}\x1b[0m")
    response = urllib.request.urlopen(url)
    csv_data = response.read().decode("utf-8")
    csv_file = io.StringIO(csv_data)
    members_and_date = []
    reader = csv.DictReader(csv_file)
    columns = reader.fieldnames
    for row in reader:
        members_and_date.append((Member(
            name=row.get("name"),
            email=row.get("email"),
            phone_number = None if row.get("phone number") == "" else row.get("phone number"),
            uni_id=row.get("uni id"),
            gender=row.get("gender")
        ), [row[n] for n in columns[5:]]))  # Collecting all date columns
    print(f"Extracted \x1b[32m{len(members_and_date)}\x1b[0m members from csv")
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
        return dev_url
    # If DEV_DATABASE_URL doesn't exist, return the string "DATABASE_URL"
    return "DATABASE_URL"


if __name__ == "__main__":
    url = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRcmCPGpWxuj9y8LEjeKPhBW77qRvyWs3g5wAH5eA5weEPASXj-FvhLUwa_CNW5ZX9D6c3qyOk5bej0/pub?gid=1781104695&single=true&output=csv"
    members = csv_to_pydantic_member(url)
    for member in members:
        print(member)