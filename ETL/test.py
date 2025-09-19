import requests
import json
from pprint import pprint

with open("test.json", "r", encoding="utf-8") as f:
    data = json.load(f)

member = { 
    "action name": "save the world", 
    "action arabic name": "انقاذ العالم",
    "action type": "member", 
    "action description": "saving the world from jews", 
    "points": 10000
}

request = requests.post("http://127.0.0.1:8000/actions", json=member)
print(f"response code", request.status_code)
pprint(request.json())
