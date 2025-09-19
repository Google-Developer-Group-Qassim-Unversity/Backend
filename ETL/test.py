import requests
import json
from pprint import pprint

with open("test.json", "r", encoding="utf-8") as f:
    data = json.load(f)

member = { 
    "name": "Batman", 
    "email": "brucewain@gotham.com",
    "phone number": "0566666666", 
    "uni id": "452106902"
}

request = requests.post("http://127.0.0.1:8000/members", json=member)
print(f"response code", request.status_code)
pprint(request.json())
