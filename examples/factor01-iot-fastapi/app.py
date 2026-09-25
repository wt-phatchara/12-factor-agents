"""
Factor 1, wired into FastAPI: POST /command takes a natural-language IoT
command, converts it to a structured tool call, then deterministically
dispatches it (MQTT publish / dry-run). Same nl_to_mqtt.py logic underneath —
this file is just the HTTP boundary.

Run it:
    pip install fastapi uvicorn "pydantic>=2"
    uvicorn app:app --reload --port 8000

Try it (mock mode, no ANTHROPIC_API_KEY needed):
    curl -X POST localhost:8000/command \
        -H "Content-Type: application/json" \
        -d '{"text": "turn on the greenhouse pump for 10 seconds"}'

    curl -X POST localhost:8000/command \
        -H "Content-Type: application/json" \
        -d '{"text": "open the front gate"}'
    # -> 422, unknown device, rejected by the deterministic dispatcher

Interactive API docs: http://localhost:8000/docs
"""

from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from nl_to_mqtt import (
    KNOWN_DEVICES,
    UnknownDeviceError,
    dispatch,
    nl_to_tool_call,
)

app = FastAPI(
    title="12-Factor Agents · Factor 1 demo",
    description="Natural language -> structured tool call -> deterministic MQTT dispatch",
    version="0.1.0",
)


# ---------------------------------------------------------------------------
# Request / response schemas — the HTTP-layer contract, separate from (but
# mirroring) the tool schema the LLM fills in. Keeping these as explicit
# Pydantic models means FastAPI validates and documents them automatically,
# and callers get a real OpenAPI schema at /docs.
# ---------------------------------------------------------------------------

class CommandRequest(BaseModel):
    text: str = Field(..., min_length=1, examples=["turn on the greenhouse pump for 10 seconds"])


class ToolCall(BaseModel):
    device_id: str
    action: Literal["on", "off", "set_level"]
    level_pct: Optional[int] = None
    duration_s: Optional[int] = None


class CommandResponse(BaseModel):
    tool_call: ToolCall
    mqtt_topic: str
    mqtt_payload: str


class ErrorResponse(BaseModel):
    detail: str
    tool_call: ToolCall


@app.get("/devices")
def list_devices() -> dict:
    """Deterministic — no LLM involved. Lets a caller see what's controllable."""
    return KNOWN_DEVICES


@app.post(
    "/command",
    response_model=CommandResponse,
    responses={422: {"model": ErrorResponse, "description": "Unknown device or invalid parameters"}},
)
def issue_command(req: CommandRequest):
    # Step 1 (LLM): natural language -> structured tool call.
    raw_tool_call = nl_to_tool_call(req.text)
    tool_call = ToolCall(**raw_tool_call)

    # Step 2 (deterministic code): validate + dispatch. Never let step 1's
    # output touch hardware without this gate.
    try:
        payload = dispatch(tool_call.model_dump())
    except (UnknownDeviceError, ValueError) as e:
        return JSONResponse(
            status_code=422,
            content={"detail": str(e), "tool_call": tool_call.model_dump()},
        )

    return CommandResponse(
        tool_call=tool_call,
        mqtt_topic=f"devices/{tool_call.device_id}/cmd",
        mqtt_payload=payload,
    )
