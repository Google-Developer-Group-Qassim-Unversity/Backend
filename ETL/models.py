from pydantic import BaseModel, Field, HttpUrl
import json
from typing import List, Literal
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
    id: int | None = None
    name: str
    email: str 
    phone_number: str | None = Field(default=None, alias="phone number")
    uni_id: str = Field(alias="uni id")

    class Config:
        populate_by_name = True


class Action(BaseModel):
    action_name: str = Field(alias="action name")
    action_arabic_name: str = Field(alias="action arabic name")
    action_type: Literal["composite", "department", "member"] = Field(alias="action type")
    action_description: str = Field(alias="action description")
    points: int
    
    class Config:
        populate_by_name = True