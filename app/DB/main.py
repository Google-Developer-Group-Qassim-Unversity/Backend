from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.config import config


engine = create_engine(config.DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autocommit=False, expire_on_commit=False)

