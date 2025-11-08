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
    department_discount: int
    department_bonus: int
    member_discount: int
    member_bonus: int

class CompositeEventData(Complex_EventData):
    department_id: int
    members_attendance: str
    department_action_id: int
    member_action_id: int

    @field_validator('members_attendance')
    def file_or_url(cls, v: str):
        if v.startswith('https://'):
            if "docs.google.com/spreadsheets" not in v and not v.endswith("output=csv"):
                raise ValueError("The Url must be a Google Sheets link with 'output=csv' parameter")
            else:
                return HttpUrl(v)
        elif (v.endswith('.xlsx') or v.endswith('.csv')):
            return v
        else:
            raise ValueError("members_attendance must be a valid file path ending with .xlsx or .csv, or a Google Sheets URL")


class BaseEventReport(BaseClassModel):
    event: Events_model
    days: int
    department: str
    department_points: int

class CompositeEventReport(BaseEventReport):
    members_count: int
    members_points: int

class DepartmentEventData(BaseClassModel):
    event_info: Events_model
    department_id: int
    action_id: int
    bonus: int 

class CustomeBulkMemberData(BaseClassModel):
    members_attendance: str
    action_name: str
    action_points: int

    @field_validator('members_attendance')
    def file_or_url(cls, v: str):
        if v.startswith('https://'):
            if "docs.google.com/spreadsheets" not in v and not v.endswith("output=csv"):
                raise ValueError("The Url must be a Google Sheets link with 'output=csv' parameter")
            else:
                return HttpUrl(v)
        elif (v.endswith('.xlsx') or v.endswith('.csv')):
            return v
        else:
            raise ValueError("members_attendance must be a valid file path ending with .xlsx or .csv, or a Google Sheets URL")

class CustomeBulkMemberReport(BaseClassModel):
    members_count: int
    members_points: int
    action_name: str


class CustomMemberData(Member_model):
    action_name: str
    points: int

class CustomeMemberReport(BaseClassModel):
    member_name: str
    action_name: str
    points: int

class CustomeDepartmentData(BaseClassModel):
    department_id: int
    action_name: str
    points: int

class CustomeDepartmentReport(BaseClassModel):
    department_name: str
    action_name: str
    points: int