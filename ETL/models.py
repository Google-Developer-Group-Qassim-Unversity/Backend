from pydantic import BaseModel, Field, HttpUrl
import json
from typing import List
from datetime import date

class Contributor(BaseModel):
    name: str
    email: str
    phone_number: str = Field(alias="phone number")  
    uni_id: str = Field(alias="uni id")
    action: str

    class Config:
        populate_by_name = True


class Event(BaseModel):
    action: str
    departments: str
    event_name: str = Field(alias="event name")
    start_date: date = Field(alias="start date")
    end_date: date = Field(alias="end date")
    attendants: HttpUrl
    contributors: List[Contributor]

    class Config:
        populate_by_name = True



class Member(BaseModel):
    id: int
    name: str
    email: str
    phone_number: str = Field(alias="phone number")
    uni_id: str = Field(alias="uni id")