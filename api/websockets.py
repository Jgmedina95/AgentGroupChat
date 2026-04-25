from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from db.session import SessionLocal


router = APIRouter()


class ConnectionManager:
	def __init__(self) -> None:
		self._connections: dict[str, list[WebSocket]] = defaultdict(list)

	async def connect(self, websocket: WebSocket, conversation_id: str) -> None:
		await websocket.accept()
		self._connections[conversation_id].append(websocket)

	def disconnect(self, websocket: WebSocket, conversation_id: str) -> None:
		connections = self._connections.get(conversation_id, [])
		if websocket in connections:
			connections.remove(websocket)
		if not connections and conversation_id in self._connections:
			del self._connections[conversation_id]

	async def broadcast(self, conversation_id: str, event: dict) -> None:
		stale_connections: list[WebSocket] = []
		for websocket in list(self._connections.get(conversation_id, [])):
			try:
				await websocket.send_json(event)
			except RuntimeError:
				stale_connections.append(websocket)

		for websocket in stale_connections:
			self.disconnect(websocket, conversation_id)


manager = ConnectionManager()
conversation_list_manager = ConnectionManager()


def conversation_exists(conversation_id: str) -> bool:
	db = SessionLocal()
	try:
		row = db.execute("SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
		return row is not None
	finally:
		db.close()


@router.websocket("/ws/conversations")
async def conversations_websocket(websocket: WebSocket) -> None:
	channel = "__all_conversations__"
	await conversation_list_manager.connect(websocket, channel)
	await websocket.send_json({"event": "conversations.ready"})

	try:
		while True:
			await websocket.receive_text()
	except WebSocketDisconnect:
		conversation_list_manager.disconnect(websocket, channel)


@router.websocket("/ws/conversations/{conversation_id}")
async def conversation_websocket(websocket: WebSocket, conversation_id: str) -> None:
	if not conversation_exists(conversation_id):
		await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Conversation not found")
		return

	await manager.connect(websocket, conversation_id)
	await websocket.send_json({"event": "connection.ready", "conversation_id": conversation_id})

	try:
		while True:
			await websocket.receive_text()
	except WebSocketDisconnect:
		manager.disconnect(websocket, conversation_id)
