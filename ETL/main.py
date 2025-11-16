from fastapi import FastAPI
from endpoints import router
from fastapi.middleware.cors import CORSMiddleware
from fastapi_cache import FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend
import sheet_validation as sv
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


@app.on_event("startup")
async def startup_event():
    print("--- Initializing cache... ---")
    FastAPICache.init(InMemoryBackend(), prefix="fastapi-cache")