from fastapi import FastAPI
from endpoints import router
from fastapi.middleware.cors import CORSMiddleware
import api.sheet_validation as sv
app  = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allow all HTTP methods
    allow_headers=["*"],  # Allow all HTTP headers
)

app.include_router(router)
app.include_router(sv.router)