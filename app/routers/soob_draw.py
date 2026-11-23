from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert

from app.DB.schema import SoobDrawEntries
from app.dependencies import DB
from app.helpers import CurrentMember

router = APIRouter(prefix="/soob-draw", tags=["SOOB draw"])


class Hitbox(BaseModel):
    x: float
    y: float
    width: float
    height: float


# Fractions of the full scene, measured from its top-left corner.
HITBOX = Hitbox(x=0.631, y=0.684, width=0.116, height=0.106)


class DrawEntryStatus(BaseModel):
    entered: bool


class GameStatus(DrawEntryStatus):
    hitbox: Hitbox


class Tap(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


@router.get("/entry", response_model=GameStatus)
def get_entry(member: CurrentMember, session: DB):
    entry = session.scalar(select(SoobDrawEntries.id).where(SoobDrawEntries.member_id == member.id))
    return GameStatus(entered=entry is not None, hitbox=HITBOX)


@router.post("/entry", response_model=DrawEntryStatus)
def enter_draw(tap: Tap, member: CurrentMember, session: DB):
    if not (HITBOX.x <= tap.x <= HITBOX.x + HITBOX.width and HITBOX.y <= tap.y <= HITBOX.y + HITBOX.height):
        raise HTTPException(status_code=422, detail="The tap missed the mascot")

    table = SoobDrawEntries.__table__
    statement = insert(SoobDrawEntries).values(member_id=member.id)
    session.execute(statement.on_duplicate_key_update(id=table.c.id))
    session.commit()
    return DrawEntryStatus(entered=True)
