import requests
import json
from pprint import pprint
import pandas as pd
# with open("test.json", "r", encoding="utf-8") as f:
#     data = json.load(f)

# action = { 
#     "action name": "save the world", 
#     "action arabic name": "انقاذ العالم",
#     "action type": "member", 
#     "action description": "saving the world from jews", 
#     "points": 10000
# }

# member = {
#     "id": 131, 
#     "name": "Spiderman", 
#     "email": "peterbarker@newyork.com",
#     "phone number": "05777777777", 
#     "uni id": "456345552" 
# }

# request = requests.put("http://127.0.0.1:8000/members", json=member)
# print(f"response code", request.status_code)
# pprint(request.json())


df = pd.read_csv("test.csv")

duped = df[df['uni id'].duplicated(keep=False)].index.tolist()
print(duped)