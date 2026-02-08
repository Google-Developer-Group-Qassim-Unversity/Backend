from pandas.core.indexes.base import format_object_summary
from pydantic import BaseModel, HttpUrl, EmailStr, field_validator, conlist
from typing import List, Literal
from datetime import datetime
from pydantic.types import JsonValue

class BaseClassModel(BaseModel):

    class Config:
        from_attributes = True

class Events_model(BaseClassModel):
    id: int | None = None
    name: str
    description: str | None = None
    location_type: Literal['online', 'on-site', 'none']
    location: str
    start_datetime: datetime
    end_datetime: datetime
    status: Literal['draft', 'open', 'active', 'closed']
    image_url: str | None = None
    is_official: int | None = None

class Form_model(BaseClassModel):
    id: int | None = None
    event_id: int
    form_type: Literal['google', 'none', 'registration']
    google_form_id: str | None = None
    google_refresh_token: str | None = None
    google_watch_id: str | None = None
    google_responders_url: str | None = None

class createEvent_model(BaseClassModel):
    event: Events_model
    form_type: Literal['google', 'none', 'registration']
    department_action_id: int
    member_action_id: int
    department_id: int

class event_actions_model(BaseClassModel):
    action_id: int
    ar_action_name: str
    department_id: int | None = None
    department_ar_name: str | None = None

class UpdateEvent_model(BaseClassModel):
    event: Events_model
    actions: List[event_actions_model]

class UpdateEventStatus_model(BaseClassModel):
    status: Literal['draft', 'open', 'active', 'closed']


class Open_Events_model(Events_model):
    form_type: Literal['google', 'none', 'registration']
    form_id: int
    google_responders_url: str | None = None
    

class submission_exists_model(BaseClassModel):
    submission_status: Literal[False, True, 'partial']
    submission_timestamp: datetime | None = None

class submission_accept_model(BaseClassModel):
    submission_id: int
    is_accepted: bool
    
class Member_model(BaseClassModel):
    id: int | None = None
    name: str
    email: EmailStr
    phone_number: str | None
    uni_id: str
    gender: Literal["Male", "Female"]
    uni_level: int
    uni_college: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    is_authenticated: bool | None = None

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

class MeberCreate_model(BaseClassModel):
    member: Member_model
    already_exists: bool



class Get_Submission_model(BaseClassModel):
    member: Member_model
    submission_id: int
    submitted_at: datetime
    form_type: Literal['google', 'none', 'registration']
    submission_type: Literal['none', 'partial', 'google', 'registration']
    is_accepted: bool
    google_submission_value: JsonValue | None = None
    event_id: int
    form_id: int
    google_form_id: str | None = None

class Create_Google_Submission_model(BaseClassModel):
    id: int | None = None
    form_id: int
    member_id: int
    submission_type: Literal['none', 'partial', 'google', 'registration']
    is_accepted: bool
    submitted_at: datetime | None = None
    google_submission_id: str | None = None
    google_submission_value: JsonValue | None = None

class MemberHistory_model(BaseClassModel):
    name: str
    description: str | None = None
    location: str
    location_type: Literal["online", "on-site"]
    start_datetime: datetime
    end_datetime: datetime
    action_name: str
   
class Department_model(BaseClassModel):
    id: int | None = None
    name: str
    ar_name: str
    type: Literal['administrative', 'practical']



class Action_model(BaseClassModel):
    id: int 
    action_name: str
    ar_action_name: str 
    action_type: Literal["composite", "department", "member", "bonus"] 
    points: int

class Categorized_action(BaseClassModel):
    composite_actions: List[conlist(Action_model, min_length=2, max_length=2)]
    department_actions: List[Action_model]
    member_actions: List[Action_model] 
    custom_actions: List[Action_model]

class manual_members(BaseClassModel):
    members_sheet: HttpUrl

class ConflictResponse(BaseClassModel):
    detail: str

class NotFoundResponse(BaseClassModel):
    detail: str

class BadRequestResponse(BaseClassModel):
    detail: str

class InternalServerErrorResponse(BaseClassModel):
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

class CardData(BaseClassModel):
    name: str
    url: str
