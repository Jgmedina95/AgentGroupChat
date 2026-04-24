from fastapi import WebSocket

active_connections = {}

async def connect(websocket: WebSocket, conversation_id: str):
    await websocket.accept()
    active_connections.setdefault(conversation_id, []).append(websocket)

async def broadcast(conversation_id: str, message: dict):
    for ws in active_connections.get(conversation_id, []):
        await ws.send_json(message)