from fastapi import FastAPI

from api.routes import router
from api.websockets import router as websocket_router
from db.base import Base
from db.session import engine
from models import Agent, Conversation, Message


app = FastAPI(title="Agent Group Chat")
app.include_router(router)
app.include_router(websocket_router)


@app.on_event("startup")
def startup() -> None:
	Base.metadata.create_all(bind=engine)


@app.get("/health")
def health() -> dict[str, str]:
	return {"status": "ok"}
