from pydantic import BaseModel, Field, HttpUrl
import json
from typing import List, Literal, Tuple, Optional
from datetime import datetime, date
from fastapi import Form, UploadFile, File, HTTPException, status
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
    uni_id: str | int
    email: str | None
    phone_number: str | None
    participation_action_id: str
    gender: Literal["Male", "Female"]
    attendance: List[Literal["present", "absent"]]

    class Config:
        populate_by_name = True


class DepartmentFormData(BaseModel):
    action: Literal["department"]
    event_info: EventData
    department_id: int
    organizers: List[OrganizerData] | None 
    action_id: int
    bonus: int 
    discount: int
    

class CompositeFormData(BaseModel):
    action: Literal["composite"]
    event_info: EventData
    department_id: int
    members_link: HttpUrl | None = Field(alias="members link")
    organizers: List[OrganizerData] | None
    department_action_id: int
    member_action_id: int
    bonus: int
    discount: int
    class Config:
        populate_by_name = True


class Member(BaseModel):
    id: int | None = None
    name: str
    email: str 
    phone_number: str | None = Field(default=None, alias="phone number")
    uni_id: str
    gender: Literal["Male", "Female"]

    class Config:
        populate_by_name = True
        from_atrributes = True



class MemberFormData(BaseModel):
    event_info: EventData
    members: List[Member]
    bonus: int
    discount: int 
    action_id: int


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
    composite_actions: List[List[Action]] = Field(alias="composite action", description="List of pairs of actions, each inner list should have length 2")
    department_actions: List[Action] = Field(alias="department action")
    member_actions: List[Action] = Field(alias="member action")
    custom_actions: List[Action] = Field(alias="custom action")

    class Config:
        populate_by_name = True
        from_attributes = True


class ValidateSheet(BaseModel):
    url: HttpUrl | None = None
    start_date: datetime = Field(alias="start date") 
    end_date: datetime = Field(alias="end date")

    class Config:
        populate_by_name = True 

    @property
    def url_str(self) -> str:
        return str(self.url)
    
def parse_validate_sheet(
    # One of these two should be provided:
    url: Optional[HttpUrl] = Form(None),
    file: Optional[UploadFile] = File(None),
    start_date: datetime = Form(..., alias="start date"),
    end_date: datetime = Form(..., alias="end date"),
) -> tuple[ValidateSheet, Optional[UploadFile]]:
    # Must have either url or file, not both nor neither
    if (url and file) or (not url and not file):
        raise HTTPException(
            status_code=400,
            detail="Provide either 'url' or 'file', but not both or neither."
        )
    data = ValidateSheet(
        url=url,
        start_date=start_date,
        end_date=end_date
    )
    return data, file
    


class CustomMembersFormData(BaseModel):
    event_info: EventData
    members: List[Member]
    bonus: int
    action_id: int

class CustomDepartmentsFormData(BaseModel):
    event_info: EventData
    department_id: int
    bonus: int
    action_id: int


def parse_composite_form(
    action: str = Form(...),
    event_info: str = Form(...),
    department_id: int = Form(...),
    department_action_id: int = Form(...),
    member_action_id: int = Form(...),
    bonus: int = Form(...),
    discount: int = Form(...),
    members_link: HttpUrl | None = Form(None, alias="members link"),
    organizers: Optional[str] = Form(None),
    members_file: Optional[UploadFile] = File(None),
) -> Tuple[CompositeFormData, Optional[UploadFile]]:
    # Validate action
    if action != "composite":
        raise HTTPException(
            status_code=422,
            detail=f"Invalid action: expected 'composite', got '{action}'"
        )

    # Parse event_info JSON string to EventData
    try:
        event_info_obj = EventData(**json.loads(event_info))
    except Exception as e:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid event_info: {str(e)}"
        )

    # Parse organizers if provided
    organizers_obj = None
    if organizers:
        try:
            orgs_parsed = json.loads(organizers)
            organizers_obj = [OrganizerData(**item) for item in orgs_parsed]
        except Exception as e:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid organizers: {str(e)}"
            )
    
    # Ensure either members_link OR members_file (but not both/neither)
    if (members_link and members_file) or (not members_link and not members_file):
        raise HTTPException(
            status_code=400,
            detail="Must provide either 'members_link' or 'members_file', but not both or neither."
        )

    composite_form = CompositeFormData(
        action=action,
        event_info=event_info_obj,
        department_id=department_id,
        organizers=organizers_obj,
        department_action_id=department_action_id,
        member_action_id=member_action_id,
        bonus=bonus,
        discount=discount,
        members_link=members_link
    )
    return composite_form, members_file