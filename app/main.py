from fastapi import FastAPI
from app.routers import upload, members, events, departments, action, complex_events, custom, edit, auth
from fastapi.middleware.cors import CORSMiddleware


app  = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(members.router, prefix="/members", tags=["members"])
app.include_router(upload.router, prefix="/upload", tags=["upload"])
app.include_router(events.router, prefix="/events", tags=["events"])
app.include_router(complex_events.router, prefix="/events", tags=["complex_events"])
app.include_router(departments.router, prefix="/departments", tags=["departments"])
app.include_router(action.router, prefix="/actions", tags=["actions"])
app.include_router(custom.router, prefix="/custom", tags=["custom"])
app.include_router(edit.router, prefix="/edit", tags=["edit"])
app.include_router(auth.router, tags=["auth"])
