from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from app.routers import (
    upload, members, events, departments,
     action, complex_events, custom, edit,
     auth, card, forms, submissions,
     submissions_manual, points
)
from app.config import config

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs")



app.include_router(members.router, prefix="/members", tags=["members"])
app.include_router(upload.router, prefix="/upload", tags=["upload"])
app.include_router(events.router, prefix="/events", tags=["events"])
app.include_router(complex_events.router, prefix="/events", tags=["complex_events"])
app.include_router(departments.router, prefix="/departments", tags=["departments"])
app.include_router(action.router, prefix="/actions", tags=["actions"])
app.include_router(custom.router, prefix="/custom", tags=["custom"])
app.include_router(edit.router, prefix="/edit", tags=["edit"])
app.include_router(auth.router, tags=["auth"])
app.include_router(card.router, prefix="/card", tags=["Card"])
app.include_router(forms.router, prefix="/forms", tags=["Forms"])
app.include_router(submissions.router, prefix="/submissions", tags=["Submissions"])
app.include_router(submissions_manual.router, prefix="/submissions_manual", tags=["Submissions Manual"])
app.include_router(points.router, prefix="/points", tags=["Points"])

# TODO: replace this with nginx for production
app.mount("/files", StaticFiles(directory=config.UPLOAD_DIR), name="files")