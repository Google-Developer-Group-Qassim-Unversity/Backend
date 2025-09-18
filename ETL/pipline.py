from dotenv import load_dotenv
from os import getenv
from sqlalchemy import select 
from fastapi import FastAPI
from db import *
from models import Event
from pprint import pprint

app = FastAPI()

@app.post("/etl")
def main(event: Event):
    pprint(event.model_dump(), indent=4)

    return {"status": "success"}
