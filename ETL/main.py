from fastapi import FastAPI
from endpoints import router
from fastapi.middleware.cors import CORSMiddleware
import sheet_validation as sv

# Caching setup taken from the official 'fastapi-cache' example at "https://github.com/long2ice/fastapi-cache/blob/main/examples/in_memory/main.py"
from contextlib import asynccontextmanager
from typing import AsyncIterator
from fastapi_cache import FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend

@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    FastAPICache.init(InMemoryBackend())
    yield

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allow all HTTP methods
    allow_headers=["*"],  # Allow all HTTP headers
)

app.include_router(router)
app.include_router(sv.router)

