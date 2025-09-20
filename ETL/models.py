from pydantic import BaseModel, Field, HttpUrl
import json
from typing import List, Literal
from datetime import datetime, date

class Contributor(BaseModel):
    name: str
    email: str
    phone_number: str = Field(alias="phone number")  
    uni_id: str # @ibrahim do not add alias.
    action: str

    class Config:
        populate_by_name = True


class Event(BaseModel):
    action: str
    department: str
    event_name: str = Field(alias="event name")
    start_date: date = Field(alias="start date")
    end_date: date = Field(alias="end date")
    attendants: HttpUrl | None = None
    contributors: List[Contributor] | None = None

    class Config:
        populate_by_name = True

class EventData(BaseModel):
    event_title: str
    start_date: datetime
    end_date: datetime

    class Config:
        populate_by_name = True

class OrganizerData(BaseModel):
    name: str
    uni_id: str
    email: str | None
    phone_number: str | None
    participation_type: str

    class Config:
        populate_by_name = True

class FormData(BaseModel):
    action: Literal["composite", "department", "member"]
    event_info: EventData
    department_id: str
    members_link: HttpUrl = Field(alias="members link")
    Organizers: List[OrganizerData] | None
    action_id: int
    class Config:
        populate_by_name = True


class Member(BaseModel):
    id: int | None = None
    name: str
    email: str 
    phone_number: str | None = Field(default=None, alias="phone number")
    uni_id: str # @ibrahim do not add alias.
    gender: Literal["Male", "Female"]

    class Config:
        populate_by_name = True
        from_atrributes = True

class Department(BaseModel):
    id: int
    name: str

    class Config:
        populate_by_name = True
        from_atrributes = True


class Action(BaseModel):
    
    id: int 
    action_name: str = Field(alias="action name")
    arabic_action_name: str = Field(alias="action arabic name")
    action_type: Literal["composite", "department", "member"] = Field(alias="action type")
    action_description: str = Field(alias="action description")
    points: int
    
    class Config:
        populate_by_name = True
        from_attributes = True


class Categorized_action(BaseModel):
    composite_actions: List[Action] = Field(alias="composite action")
    department_actions: List[Action] = Field(alias="department action")
    member_actions: List[Action] = Field(alias="member action")

    class Config:
        populate_by_name = True
        from_attributes = True


class ValidateSheet(BaseModel):
    url: HttpUrl
    start_date: datetime = Field(alias="start date") 
    end_date: datetime = Field(alias="end date")

    class Config:
        populate_by_name = True 

    @property
    def url_str(self) -> str:
        return str(self.url)