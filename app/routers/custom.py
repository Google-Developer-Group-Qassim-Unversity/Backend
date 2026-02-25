from typing import List
from fastapi import APIRouter, HTTPException, status, Depends
from fastapi.security import HTTPAuthorizationCredentials
from app.DB import (
    events as events_queries,
    departments as departments_queries,
    actions as actions_queries,
    logs as log_queries,
    members as members_queries,
)
from app.DB.main import SessionLocal
from app.routers.models import Events_model, BaseClassModel
from app.helpers import get_pydantic_members, admin_guard
from datetime import timedelta, datetime
from app.routers.logging import (
    write_log,
    write_log_traceback,
    create_log_file,
    write_log_exception,
    write_log_json,
    write_log_title,
)
from app.routers.models import LocationTypeEnum
import json

router = APIRouter()


class DepartmentPointDetails(BaseClassModel):
    departments_id: list[int]
    points: int
    action_id: int | None = None
    action_name: str | None = None


class MemberPointDetails(BaseClassModel):
    member_ids: list[int]
    points: int
    action_id: int | None = None
    action_name: str | None = None


class CustomDepartmentPointsRequest(BaseClassModel):
    event_id: int | None = None
    start_datetime: datetime
    end_datetime: datetime
    event_name: str
    location_type: LocationTypeEnum
    point_deatils: List[DepartmentPointDetails]


class CustomMemberPointsRequest(BaseClassModel):
    event_id: int | None = None
    start_datetime: datetime
    end_datetime: datetime
    event_name: str
    location_type: LocationTypeEnum
    point_deatils: List[MemberPointDetails]


class DepartmentPointDetailsWithLogId(BaseClassModel):
    log_id: int
    departments_id: list[int]
    points: int
    action_id: int | None = None
    action_name: str | None = None


class MemberPointDetailsWithLogId(BaseClassModel):
    log_id: int
    member_ids: list[int]
    points: int
    action_id: int | None = None
    action_name: str | None = None


class CustomDepartmentPointsResponse(BaseClassModel):
    event_id: int
    start_datetime: datetime
    end_datetime: datetime
    event_name: str
    point_details: List[DepartmentPointDetailsWithLogId]


class CustomMemberPointsResponse(BaseClassModel):
    event_id: int
    start_datetime: datetime
    end_datetime: datetime
    event_name: str
    point_details: List[MemberPointDetailsWithLogId]


@router.post("/departments", status_code=status.HTTP_201_CREATED)
def give_department_custom_points(
    body: CustomDepartmentPointsRequest,
    credentials: HTTPAuthorizationCredentials = Depends(admin_guard),
):
    log_file = create_log_file("custom_department_points")
    with SessionLocal() as session:
        try:
            write_log_title(log_file, "Custom Department Points")
            # [1] validate events
            if body.event_id:
                write_log(log_file, f"Validating event with id {body.event_id}")
                event = events_queries.get_event_by_id(session, body.event_id)
                if not event:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Event with id {body.event_id} not found",
                    )
            else:
                # an existing event is not provided, create a new one with default values
                new_event_model = Events_model(
                    name=body.event_name,
                    description=None,
                    location="none",
                    location_type=body.location_type,
                    start_datetime=body.start_datetime,
                    end_datetime=body.end_datetime,
                    status="closed",
                    image_url=None,
                    is_official=0,
                )

                event = events_queries.create_event(session, new_event_model)
                if not event:
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Failed to create event",
                    )

            # Proccess each point detail and give points to departments per action/points
            details_len = len(body.point_deatils)
            for i, point_detail in enumerate(body.point_deatils):
                # [2] validate action
                write_log(log_file, f"Processing point detail [{i + 1}/{details_len}]")
                if point_detail.action_id:
                    write_log(
                        log_file, f"Validating action with id {point_detail.action_id}"
                    )
                    action = actions_queries.get_action_by_id(
                        session, point_detail.action_id
                    )
                    if not action:
                        raise HTTPException(
                            status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Action with id {point_detail.action_id} not found",
                        )
                else:
                    write_log(log_file, f"No action id provided, checking name")
                    if point_detail.action_name:
                        write_log(
                            log_file,
                            f"Action name provided creating action with name {point_detail.action_name}",
                        )
                        action = actions_queries.create_action(
                            session,
                            point_detail.action_name,
                            point_detail.points,
                            "bonus",
                        )
                    else:
                        write_log(
                            log_file,
                            f"No action name provided, inferring from points value",
                        )
                        if point_detail.points > 0:
                            action = actions_queries.get_bonus_action(session)
                            write_log(
                                log_file,
                                f"Points are {point_detail.points}, using Bonus action {action.action_name} with id {action.id}",
                            )
                        else:
                            action = actions_queries.get_discount_action(session)
                            write_log(
                                log_file,
                                f"Points are {point_detail.points}, using Discount action {action.action_name} with id {action.id}",
                            )

                # [3] using the action and event to give points
                new_log = log_queries.create_log(session, event.id, action.id)
                write_log(
                    log_file,
                    f"Created log with id {new_log.id} for event {event.name} and action {action.action_name}",
                )

                # [4] creating bonus/discount modification - only for default actions
                # When action_id is provided: predefined action with its own points
                # When action_name is provided: new custom action created with points embedded
                # Only create modification when using default Bonus/Discount (no action_id AND no action_name)
                if point_detail.action_id is None and point_detail.action_name is None:
                    mod_type = "bonus" if point_detail.points > 0 else "discount"
                    mod_value = abs(point_detail.points)
                    log_queries.create_modification(
                        session, new_log.id, mod_type, mod_value
                    )
                    write_log(
                        log_file,
                        f"Created modification for log id {new_log.id} with type {mod_type} and value {mod_value}",
                    )
                else:
                    write_log(
                        log_file,
                        f"Skipping modification - using action points ({action.points})",
                    )

                # [5] give points to departments
                for department_id in point_detail.departments_id:
                    write_log(
                        log_file, f"Giving points to department with id {department_id}"
                    )
                    department_log = log_queries.create_department_log(
                        session, department_id, new_log.id
                    )

            session.commit()
            write_log(log_file, "Successfully given custom points to departments")
            return {"message": "Successfully given custom points to departments"}

        except Exception as e:
            session.rollback()
            write_log_exception(log_file, e)
            write_log_traceback(log_file)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An error occurred while giving custom points to the department",
            )
        finally:
            write_log_json(log_file, body.model_dump(mode="json"))
            write_log(log_file, "Finished processing custom department points request")


@router.get("/departments/{event_id}", response_model=CustomDepartmentPointsResponse)
def get_department_custom_points(
    event_id: int, credentials: HTTPAuthorizationCredentials = Depends(admin_guard)
):
    """Retrieve all custom department points for a specific event."""
    log_file = create_log_file("get_custom_department_points")
    with SessionLocal() as session:
        try:
            write_log_title(
                log_file, f"Get Custom Department Points for Event {event_id}"
            )

            # [1] Validate event exists
            write_log(log_file, f"Validating event with id {event_id}")
            event = events_queries.get_event_by_id(session, event_id)
            if not event:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Event with id {event_id} not found",
                )

            # [2] Get all custom department points for this event
            write_log(
                log_file, f"Fetching custom department points for event {event_id}"
            )
            raw_points = log_queries.get_custom_department_points_by_event(
                session, event_id
            )

            # [3] Transform the data to match the response model
            point_details = []
            for point_data in raw_points:
                points = (
                    point_data["mod_value"]
                    if point_data["mod_type"] == "bonus"
                    else -point_data["mod_value"]
                )
                point_details.append(
                    {
                        "log_id": point_data["log_id"],
                        "departments_id": point_data["department_ids"],
                        "points": points,
                        "action_id": point_data["action_id"],
                        "action_name": point_data["action_name"],
                    }
                )

            write_log(
                log_file,
                f"Found {len(point_details)} custom point entries for event {event_id}",
            )
            response = CustomDepartmentPointsResponse(
                event_id=event.id,
                start_datetime=event.start_datetime,
                end_datetime=event.end_datetime,
                event_name=event.name,
                point_details=point_details,
            )
            write_log(log_file, "Successfully retrieved custom department points")
            return response

        except HTTPException:
            raise
        except Exception as e:
            write_log_exception(log_file, e)
            write_log_traceback(log_file)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An error occurred while retrieving custom department points",
            )


@router.put("/departments/{log_id}")
def update_department_custom_points(
    log_id: int,
    body: DepartmentPointDetails,
    credentials: HTTPAuthorizationCredentials = Depends(admin_guard),
):
    """Update a single custom department point entry by log_id."""
    log_file = create_log_file("update_custom_department_points")
    with SessionLocal() as session:
        try:
            write_log_title(
                log_file, f"Update Custom Department Points for Log {log_id}"
            )

            # [1] Validate log exists
            write_log(log_file, f"Validating log with id {log_id}")
            existing_log = log_queries.get_log_by_id(session, log_id)
            if not existing_log:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Log with id {log_id} not found",
                )
            write_log(
                log_file,
                f"Log found: event_id={existing_log.event_id}, action_id={existing_log.action_id}",
            )

            # [2] Validate or infer action
            if body.action_id:
                write_log(log_file, f"Validating action with id {body.action_id}")
                action = actions_queries.get_action_by_id(session, body.action_id)
                if not action:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Action with id {body.action_id} not found",
                    )
            else:
                write_log(
                    log_file, f"No action id provided, inferring from points value"
                )
                if body.points > 0:
                    action = actions_queries.get_bonus_action(session)
                    write_log(
                        log_file,
                        f"Points are {body.points}, using Bonus action {action.action_name} with id {action.id}",
                    )
                else:
                    action = actions_queries.get_discount_action(session)
                    write_log(
                        log_file,
                        f"Points are {body.points}, using Discount action {action.action_name} with id {action.id}",
                    )

            # [3] Validate all departments exist
            write_log(log_file, f"Validating {len(body.departments_id)} departments")
            for dept_id in body.departments_id:
                department = departments_queries.get_department_by_id(session, dept_id)
                if not department:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Department with id {dept_id} not found",
                    )

            # [4] Update the log's action if it changed
            if existing_log.action_id != action.id:
                write_log(
                    log_file,
                    f"Updating log action from {existing_log.action_id} to {action.id}",
                )
                log_queries.update_log_action_id(session, log_id, action.id)

            # [5] Update the modification - only for default actions (no action_id AND no action_name)
            # When action_id is provided: predefined action with its own points
            # When action_name is provided: custom action (created or existing) with points embedded
            modification = log_queries.get_modification_by_log_id(session, log_id)
            if body.action_id is None and body.action_name is None:
                # Default action: need a modification for points
                mod_type = "bonus" if body.points > 0 else "discount"
                mod_value = abs(body.points)
                if modification:
                    write_log(
                        log_file,
                        f"Updating modification for log {log_id}: type={mod_type}, value={mod_value}",
                    )
                    log_queries.update_modification(
                        session, modification.id, mod_type, mod_value
                    )
                else:
                    write_log(log_file, f"No modification found, creating new one")
                    log_queries.create_modification(
                        session, log_id, mod_type, mod_value
                    )
            else:
                # Custom/predefined action: remove any existing modification (action has its own points)
                if modification:
                    write_log(
                        log_file,
                        f"Removing modification for log {log_id} - using action points ({action.points})",
                    )
                    log_queries.delete_modification(session, modification.id)
                else:
                    write_log(
                        log_file,
                        f"No modification to remove - using action points ({action.points})",
                    )

            # [6] Update department associations
            deleted_count = log_queries.delete_department_logs_by_log_id(
                session, log_id
            )
            write_log(
                log_file, f"Deleted {deleted_count} existing department associations"
            )

            write_log(
                log_file,
                f"Creating {len(body.departments_id)} new department associations",
            )
            for dept_id in body.departments_id:
                log_queries.create_department_log(session, dept_id, log_id)
                write_log(
                    log_file, f"Associated department {dept_id} with log {log_id}"
                )

            session.commit()
            write_log(log_file, "Successfully updated custom department points")
            return {"message": "Successfully updated custom department points"}

        except HTTPException:
            session.rollback()
            raise
        except Exception as e:
            session.rollback()
            write_log_exception(log_file, e)
            write_log_traceback(log_file)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An error occurred while updating custom department points",
            )


@router.post("/members", status_code=status.HTTP_201_CREATED)
def give_member_custom_points(
    body: CustomMemberPointsRequest,
    credentials: HTTPAuthorizationCredentials = Depends(admin_guard),
):
    log_file = create_log_file("custom_member_points")
    with SessionLocal() as session:
        try:
            write_log_title(log_file, "Custom Member Points")
            if body.event_id:
                write_log(log_file, f"Validating event with id {body.event_id}")
                event = events_queries.get_event_by_id(session, body.event_id)
                if not event:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Event with id {body.event_id} not found",
                    )
            else:
                new_event_model = Events_model(
                    name=body.event_name,
                    description=None,
                    location="none",
                    location_type=body.location_type,
                    start_datetime=body.start_datetime,
                    end_datetime=body.end_datetime,
                    status="closed",
                    image_url=None,
                    is_official=0,
                )
                event = events_queries.create_event(session, new_event_model)
                if not event:
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Failed to create event",
                    )

            details_len = len(body.point_deatils)
            for i, point_detail in enumerate(body.point_deatils):
                write_log(log_file, f"Processing point detail [{i + 1}/{details_len}]")
                if point_detail.action_id:
                    write_log(
                        log_file, f"Validating action with id {point_detail.action_id}"
                    )
                    action = actions_queries.get_action_by_id(
                        session, point_detail.action_id
                    )
                    if not action:
                        raise HTTPException(
                            status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Action with id {point_detail.action_id} not found",
                        )
                else:
                    write_log(log_file, f"No action id provided, checking name")
                    if point_detail.action_name:
                        write_log(
                            log_file,
                            f"Action name provided creating action with name {point_detail.action_name}",
                        )
                        action = actions_queries.create_action(
                            session,
                            point_detail.action_name,
                            point_detail.points,
                            "bonus",
                        )
                    else:
                        write_log(
                            log_file,
                            f"No action name provided, inferring from points value",
                        )
                        if point_detail.points > 0:
                            action = actions_queries.get_bonus_action(session)
                            write_log(
                                log_file,
                                f"Points are {point_detail.points}, using Bonus action {action.action_name} with id {action.id}",
                            )
                        else:
                            action = actions_queries.get_discount_action(session)
                            write_log(
                                log_file,
                                f"Points are {point_detail.points}, using Discount action {action.action_name} with id {action.id}",
                            )

                new_log = log_queries.create_log(session, event.id, action.id)
                write_log(
                    log_file,
                    f"Created log with id {new_log.id} for event {event.name} and action {action.action_name}",
                )

                if point_detail.action_id is None and point_detail.action_name is None:
                    mod_type = "bonus" if point_detail.points > 0 else "discount"
                    mod_value = abs(point_detail.points)
                    log_queries.create_modification(
                        session, new_log.id, mod_type, mod_value
                    )
                    write_log(
                        log_file,
                        f"Created modification for log id {new_log.id} with type {mod_type} and value {mod_value}",
                    )
                else:
                    write_log(
                        log_file,
                        f"Skipping modification - using action points ({action.points})",
                    )

                for member_id in point_detail.member_ids:
                    write_log(log_file, f"Giving points to member with id {member_id}")
                    log_queries.create_member_log(session, member_id, new_log.id)

            session.commit()
            write_log(log_file, "Successfully given custom points to members")
            return {"message": "Successfully given custom points to members"}

        except Exception as e:
            session.rollback()
            write_log_exception(log_file, e)
            write_log_traceback(log_file)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An error occurred while giving custom points to the members",
            )
        finally:
            write_log_json(log_file, body.model_dump(mode="json"))
            write_log(log_file, "Finished processing custom member points request")


@router.get("/members/{event_id}", response_model=CustomMemberPointsResponse)
def get_member_custom_points(
    event_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(admin_guard),
):
    log_file = create_log_file("get_custom_member_points")
    with SessionLocal() as session:
        try:
            write_log_title(log_file, f"Get Custom Member Points for Event {event_id}")

            write_log(log_file, f"Validating event with id {event_id}")
            event = events_queries.get_event_by_id(session, event_id)
            if not event:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Event with id {event_id} not found",
                )

            write_log(log_file, f"Fetching custom member points for event {event_id}")
            raw_points = log_queries.get_custom_member_points_by_event(
                session, event_id
            )

            point_details = []
            for point_data in raw_points:
                points = (
                    point_data["mod_value"]
                    if point_data["mod_type"] == "bonus"
                    else -point_data["mod_value"]
                )
                point_details.append(
                    {
                        "log_id": point_data["log_id"],
                        "member_ids": point_data["member_ids"],
                        "points": points,
                        "action_id": point_data["action_id"],
                        "action_name": point_data["action_name"],
                    }
                )

            write_log(
                log_file,
                f"Found {len(point_details)} custom point entries for event {event_id}",
            )
            response = CustomMemberPointsResponse(
                event_id=event.id,
                start_datetime=event.start_datetime,
                end_datetime=event.end_datetime,
                event_name=event.name,
                point_details=point_details,
            )
            write_log(log_file, "Successfully retrieved custom member points")
            return response

        except HTTPException:
            raise
        except Exception as e:
            write_log_exception(log_file, e)
            write_log_traceback(log_file)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An error occurred while retrieving custom member points",
            )


@router.delete("/departments/{log_id}")
def delete_department_custom_points(
    log_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(admin_guard),
):
    log_file = create_log_file("delete_custom_department_points")
    with SessionLocal() as session:
        try:
            write_log_title(
                log_file, f"Delete Custom Department Points for Log {log_id}"
            )

            write_log(log_file, f"Validating log with id {log_id}")
            existing_log = log_queries.get_log_by_id(session, log_id)
            if not existing_log:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Log with id {log_id} not found",
                )

            write_log(
                log_file,
                f"Log found: event_id={existing_log.event_id}, action_id={existing_log.action_id}",
            )

            deleted_mod_count = 0
            modification = log_queries.get_modification_by_log_id(session, log_id)
            if modification:
                write_log(
                    log_file,
                    f"Deleting modification with id {modification.id}",
                )
                log_queries.delete_modification(session, modification.id)
                deleted_mod_count = 1

            deleted_dept_count = log_queries.delete_department_logs_by_log_id(
                session, log_id
            )
            write_log(log_file, f"Deleted {deleted_dept_count} department associations")

            log_queries.delete_log(session, log_id)
            write_log(log_file, f"Deleted log {log_id}")

            session.commit()
            write_log(log_file, "Successfully deleted custom department points")
            return {"message": "Successfully deleted custom department points"}

        except HTTPException:
            session.rollback()
            raise
        except Exception as e:
            session.rollback()
            write_log_exception(log_file, e)
            write_log_traceback(log_file)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An error occurred while deleting custom department points",
            )


@router.delete("/members/{log_id}")
def delete_member_custom_points(
    log_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(admin_guard),
):
    log_file = create_log_file("delete_custom_member_points")
    with SessionLocal() as session:
        try:
            write_log_title(log_file, f"Delete Custom Member Points for Log {log_id}")

            write_log(log_file, f"Validating log with id {log_id}")
            existing_log = log_queries.get_log_by_id(session, log_id)
            if not existing_log:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Log with id {log_id} not found",
                )

            write_log(
                log_file,
                f"Log found: event_id={existing_log.event_id}, action_id={existing_log.action_id}",
            )

            modification = log_queries.get_modification_by_log_id(session, log_id)
            if modification:
                write_log(
                    log_file,
                    f"Deleting modification with id {modification.id}",
                )
                log_queries.delete_modification(session, modification.id)

            deleted_member_count = log_queries.delete_member_logs_by_log_id(
                session, log_id
            )
            write_log(log_file, f"Deleted {deleted_member_count} member associations")

            log_queries.delete_log(session, log_id)
            write_log(log_file, f"Deleted log {log_id}")

            session.commit()
            write_log(log_file, "Successfully deleted custom member points")
            return {"message": "Successfully deleted custom member points"}

        except HTTPException:
            session.rollback()
            raise
        except Exception as e:
            session.rollback()
            write_log_exception(log_file, e)
            write_log_traceback(log_file)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An error occurred while deleting custom member points",
            )


@router.put("/members/{log_id}")
def update_member_custom_points(
    log_id: int,
    body: MemberPointDetails,
    credentials: HTTPAuthorizationCredentials = Depends(admin_guard),
):
    log_file = create_log_file("update_custom_member_points")
    with SessionLocal() as session:
        try:
            write_log_title(log_file, f"Update Custom Member Points for Log {log_id}")

            write_log(log_file, f"Validating log with id {log_id}")
            existing_log = log_queries.get_log_by_id(session, log_id)
            if not existing_log:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Log with id {log_id} not found",
                )
            write_log(
                log_file,
                f"Log found: event_id={existing_log.event_id}, action_id={existing_log.action_id}",
            )

            if body.action_id:
                write_log(log_file, f"Validating action with id {body.action_id}")
                action = actions_queries.get_action_by_id(session, body.action_id)
                if not action:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Action with id {body.action_id} not found",
                    )
            else:
                write_log(
                    log_file, f"No action id provided, inferring from points value"
                )
                if body.points > 0:
                    action = actions_queries.get_bonus_action(session)
                    write_log(
                        log_file,
                        f"Points are {body.points}, using Bonus action {action.action_name} with id {action.id}",
                    )
                else:
                    action = actions_queries.get_discount_action(session)
                    write_log(
                        log_file,
                        f"Points are {body.points}, using Discount action {action.action_name} with id {action.id}",
                    )

            write_log(log_file, f"Validating {len(body.member_ids)} members")
            for mem_id in body.member_ids:
                member = members_queries.get_member_by_id(session, mem_id)
                if not member:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Member with id {mem_id} not found",
                    )

            if existing_log.action_id != action.id:
                write_log(
                    log_file,
                    f"Updating log action from {existing_log.action_id} to {action.id}",
                )
                log_queries.update_log_action_id(session, log_id, action.id)

            modification = log_queries.get_modification_by_log_id(session, log_id)
            if body.action_id is None and body.action_name is None:
                mod_type = "bonus" if body.points > 0 else "discount"
                mod_value = abs(body.points)
                if modification:
                    write_log(
                        log_file,
                        f"Updating modification for log {log_id}: type={mod_type}, value={mod_value}",
                    )
                    log_queries.update_modification(
                        session, modification.id, mod_type, mod_value
                    )
                else:
                    write_log(log_file, f"No modification found, creating new one")
                    log_queries.create_modification(
                        session, log_id, mod_type, mod_value
                    )
            else:
                if modification:
                    write_log(
                        log_file,
                        f"Removing modification for log {log_id} - using action points ({action.points})",
                    )
                    log_queries.delete_modification(session, modification.id)
                else:
                    write_log(
                        log_file,
                        f"No modification to remove - using action points ({action.points})",
                    )

            deleted_count = log_queries.delete_member_logs_by_log_id(session, log_id)
            write_log(log_file, f"Deleted {deleted_count} existing member associations")

            write_log(
                log_file,
                f"Creating {len(body.member_ids)} new member associations",
            )
            for mem_id in body.member_ids:
                log_queries.create_member_log(session, mem_id, log_id)
                write_log(log_file, f"Associated member {mem_id} with log {log_id}")

            session.commit()
            write_log(log_file, "Successfully updated custom member points")
            return {"message": "Successfully updated custom member points"}

        except HTTPException:
            session.rollback()
            raise
        except Exception as e:
            session.rollback()
            write_log_exception(log_file, e)
            write_log_traceback(log_file)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An error occurred while updating custom member points",
            )
