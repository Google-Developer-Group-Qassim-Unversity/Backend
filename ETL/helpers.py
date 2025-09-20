import urllib.request
import csv
import io
from models import Member
from typing import List
def csv_to_pydantic_member(url) -> List[Member]:
    response = urllib.request.urlopen(url)
    csv_data = response.read().decode("utf-8")
    csv_file = io.StringIO(csv_data)
    members_and_date = []
    for row in csv.DictReader(csv_file):
        members_and_date.append((Member(
            name=row.get("name"),
            email=row.get("email"),
            phone_number = None if row.get("phone number") == "" else row.get("phone number"),
            uni_id=row.get("uni id"),
            gender=row.get("gender")
        ), (row.get("start date"), row.get("end date"))))
    return members_and_date

if __name__ == "__main__":
    url = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRcmCPGpWxuj9y8LEjeKPhBW77qRvyWs3g5wAH5eA5weEPASXj-FvhLUwa_CNW5ZX9D6c3qyOk5bej0/pub?gid=1781104695&single=true&output=csv"
    members = csv_to_pydantic_member(url)
    for member in members:
        print(member)