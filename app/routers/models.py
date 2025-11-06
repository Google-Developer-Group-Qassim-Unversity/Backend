from pydantic import BaseModel, HttpUrl, EmailStr, field_validator, conlist
from typing import List, Literal
from datetime import datetime

class BaseClassModel(BaseModel):

    class Config:
        from_attributes = True

class Events_model(BaseClassModel):
    id: int | None = None
    name: str
    description: str | None = None
    location_type: Literal['online', 'on-site']
    location: str
    start_datetime: datetime
    end_datetime: datetime


class Member_model(BaseClassModel):
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


class Department_model(BaseClassModel):
    id: int | None = None
    name: str
    type: Literal['administrative', 'practical']


class Action_model(BaseClassModel):
    id: int 
    action_name: str
    arabic_action_name: str 
    action_type: Literal["composite", "department", "member"] 
    action_description: str 
    points: int

    


class Categorized_action(BaseClassModel):
    composite_actions: List[conlist(Action_model, min_length=2, max_length=2)]
    department_actions: List[Action_model]
    member_actions: List[Action_model] 
    custom_actions: List[Action_model]



class ConflictResponse(BaseClassModel):
    detail: str

class NotFoundResponse(BaseClassModel):
    detail: str


class Complex_EventData(BaseClassModel):
    event_info: Events_model
    discount: int
    bonus: int

class CompositeEventData(Complex_EventData):
    department_id: int
    members_attendance: HttpUrl | str
    department_action_id: int
    member_action_id: int

class CompositeEventReport(BaseClassModel):
    event: Events_model
    days: int
    members_count: int
    members_points: int
    department_points: int