import requests
import json
from pprint import pprint

with open("test.json", "r", encoding="utf-8") as f:
    data = json.load(f)

member = {
    "id": 122, 
    "name": "Superman", 
    "email": "superman@cryptonite.com",
    "phone number": "05555555555", 
    "uni id": "452106906"
}

request = requests.get("http://127.0.0.1:8000/actions")
# print(f"response code", request.status_code, request.text)

actions = request.json()["actions"]

for action in actions:
    pprint(action)