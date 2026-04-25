from fastapi import FastAPI

from api.routes import router
from api.websockets import router as websocket_router
from db.session import init_db


app = FastAPI(title="Agent Group Chat")
app.include_router(router)
app.include_router(websocket_router)


@app.on_event("startup")
def startup() -> None:
	init_db()


@app.get("/health")
def health() -> dict[str, str]:
	return {"status": "ok"}
