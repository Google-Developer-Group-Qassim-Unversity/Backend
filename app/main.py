from fastapi import FastAPI
from app.routers import upload, members, events, departments
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from os import getenv
from dotenv import load_dotenv
from helpers import get_database_url


app  = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(members.router, prefix="/members", tags=["members"])
app.include_router(upload.router, prefix="/upload", tags=["upload"])
app.include_router(events.router, prefix="/events", tags=["events"])
app.include_router(departments.router, prefix="/departments", tags=["departments"])