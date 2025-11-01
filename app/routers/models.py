from pydantic import BaseModel, Field, HttpUrl, EmailStr, field_validator
import json
from typing import List, Literal, Tuple, Optional
from datetime import datetime, date
from fastapi import HTTPException, status

class Events_model(BaseModel):
    id: int | None = None
    name: str
    description: str | None = None
    location_type: Literal['online', 'on-site']
    location: str
    start_datetime: datetime
    end_datetime: datetime

    class Config:
        from_attributes = True


class Member_model(BaseModel):
    id: int | None = None
    name: str
    email: EmailStr
    phone_number: str | None
    uni_id: int
    gender: Literal["Male", "Female"]

    @field_validator("uni_id")
    def validate_uni_id(cls, value):
        if len(str(value)) != 9:
            raise ValueError("uni_id must be a 9-digit integer")
        return value

    # This validator is needed but We have some fucked up data in the database
    # so i'll will comment it out for now.

    # @field_validator("phone_number")
    # def validate_phone_number(cls, value):
    #     if not value:
    #         return value
    #     if len(str(value)) != 10:
    #         raise ValueError("phone_number must contain 10 digits")
    #     return value

    class Config:
        from_attributes = True        

class Department_model(BaseModel):
    id: int | None = None
    name: str
    type: Literal['administrative', 'practical']

    class Config:
        from_attributes = True


class ConflictResponse(BaseModel):
    detail: str

class NotFoundResponse(BaseModel):
    detail: str