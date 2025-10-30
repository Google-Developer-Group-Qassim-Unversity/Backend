from fastapi import FastAPI
from app.routers import upload, members
from fastapi.middleware.cors import CORSMiddleware
app  = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allow all HTTP methods
    allow_headers=["*"],  # Allow all HTTP headers
)

app.include_router(members.router, prefix="/members", tags=["members"])
app.include_router(upload.router, prefix="/upload", tags=["upload"])