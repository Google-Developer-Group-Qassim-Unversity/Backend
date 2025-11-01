from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from os import getenv
from dotenv import load_dotenv
from helpers import get_database_url




load_dotenv()
engine = create_engine(getenv(get_database_url()))
SessionLocal = sessionmaker(bind=engine, autocommit=False, expire_on_commit=False)