"""
Factor 1: Natural Language to Tool Calls — applied to IoT device control.

The core lesson: the LLM's ONLY job is to turn a fuzzy human sentence into a
strict, validated, structured object (a "tool call"). It never touches your
actuators directly. Deterministic Python code reads that object and decides
what to actually do — publish an MQTT command, refuse it, ask for
confirmation, log it, whatever. This separation is what makes an LLM-powered
control system auditable and safe instead of "vibes-based".

Flow:
  "turn on the greenhouse pump for 10 seconds"
        |
        v  (LLM: natural language -> structured object, nothing else)
  {"function": "set_actuator", "parameters": {
      "device_id": "greenhouse-pump-1", "action": "on", "duration_s": 10}}
        |
        v  (your code: 100% deterministic, testable, no LLM involved)
  mqtt.publish("devices/greenhouse-pump-1/cmd", '{"action":"on","duration_s":10}')

Run it right now with no API key (MOCK MODE simulates the LLM's structured
output so you can see the full pipeline work end-to-end):

    python3 nl_to_mqtt.py

Run it against the real Claude API once you have a key:

    pip install anthropic
    export ANTHROPIC_API_KEY=sk-ant-...
    python3 nl_to_mqtt.py

Optionally actually publish to a real MQTT broker (else it just prints
the payload it *would* have published — dry run):

    pip install paho-mqtt
    export MQTT_BROKER=192.168.1.50
"""

import json
import os
import sys

# ---------------------------------------------------------------------------
# 1. The tool schema — this IS the contract between "natural language" and
#    "structured tool call". `strict: true` + additionalProperties: false
#    means the API guarantees the JSON that comes back validates against
#    this schema, so downstream code never has to defensively parse free text.
# ---------------------------------------------------------------------------

SET_ACTUATOR_TOOL = {
    "name": "set_actuator",
    "description": (
        "Command a single named actuator on the IoT network (pump, fan, "
        "relay, valve, LED strip, etc). Use this whenever the user asks to "
        "turn something on/off or set a level/duration."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "device_id": {
                "type": "string",
                "description": "Exact device id, e.g. 'greenhouse-pump-1'.",
            },
            "action": {
                "type": "string",
                "enum": ["on", "off", "set_level"],
            },
            "level_pct": {
                "type": ["integer", "null"],
                "description": "0-100, only used when action is 'set_level'.",
            },
            "duration_s": {
                "type": ["integer", "null"],
                "description": "Optional auto-off timer in seconds, null for indefinite.",
            },
        },
        "required": ["device_id", "action", "level_pct", "duration_s"],
        "additionalProperties": False,
    },
}

# A tiny "device registry" so the LLM (and this demo) has valid device_ids to
# choose from. In a real system this would come from your device database.
KNOWN_DEVICES = {
    "greenhouse-pump-1": "water pump for the greenhouse bed",
    "greenhouse-fan-1": "exhaust fan for the greenhouse",
    "battery-charger-relay-1": "relay controlling the LiC pack charger input",
}

SYSTEM_PROMPT = (
    "You translate a human's natural-language request about IoT devices "
    "into exactly one set_actuator tool call. Known devices:\n"
    + "\n".join(f"- {dev_id}: {desc}" for dev_id, desc in KNOWN_DEVICES.items())
    + "\nIf the request doesn't map to a known device, still call the tool "
    "with your best-guess device_id — the deterministic dispatcher (not you) "
    "is responsible for validating it and rejecting unknown devices."
)


# ---------------------------------------------------------------------------
# 2. Natural language -> structured tool call (the LLM step, factor 1)
# ---------------------------------------------------------------------------

def nl_to_tool_call(user_text: str) -> dict:
    """Returns a dict like {"device_id": ..., "action": ..., ...}.

    Uses the real Claude API if ANTHROPIC_API_KEY is set, otherwise falls
    back to a small rule-based mock so this file runs standalone.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        return _nl_to_tool_call_claude(user_text)
    print("[mock mode: no ANTHROPIC_API_KEY set — simulating the LLM's structured output]")
    return _nl_to_tool_call_mock(user_text)


def _nl_to_tool_call_claude(user_text: str) -> dict:
    import anthropic

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-opus-5",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        tools=[SET_ACTUATOR_TOOL],
        tool_choice={"type": "tool", "name": "set_actuator"},
        messages=[{"role": "user", "content": user_text}],
    )
    tool_use = next(b for b in response.content if b.type == "tool_use")
    # strict:true guarantees this validates against the schema already —
    # json is native here, no string-matching / regex on model output.
    return tool_use.input


def _nl_to_tool_call_mock(user_text: str) -> dict:
    """A stand-in for the LLM so the pipeline is runnable with zero setup.

    This is intentionally dumb (keyword matching) — it exists only to prove
    out the *downstream* deterministic dispatch code, which is the real
    point of factor 1. Swap this for the real API call above when ready.
    """
    text = user_text.lower()
    if "pump" in text:
        device_id = "greenhouse-pump-1"
    elif "fan" in text:
        device_id = "greenhouse-fan-1"
    elif "charger" in text or "battery" in text:
        device_id = "battery-charger-relay-1"
    else:
        device_id = "unknown-device"

    action = "on" if ("on" in text or "start" in text or "turn on" in text) else "off"
    if "%" in text or "level" in text or "set to" in text:
        action = "set_level"

    import re
    level_match = re.search(r"(\d+)\s*%", text)
    duration_match = re.search(r"(\d+)\s*(second|sec|s\b)", text)

    return {
        "device_id": device_id,
        "action": action,
        "level_pct": int(level_match.group(1)) if level_match else None,
        "duration_s": int(duration_match.group(1)) if duration_match else None,
    }


# ---------------------------------------------------------------------------
# 3. Structured tool call -> deterministic action (NOT the LLM's job)
#    This is where you validate, authorize, and actually do something.
#    The LLM never runs this code and never sees its result unless you
#    choose to loop it back in (see the note at the bottom of factor 1).
# ---------------------------------------------------------------------------

class UnknownDeviceError(ValueError):
    pass


def dispatch(tool_call: dict) -> str:
    device_id = tool_call["device_id"]
    action = tool_call["action"]

    if device_id not in KNOWN_DEVICES:
        raise UnknownDeviceError(f"refusing to command unregistered device_id={device_id!r}")

    payload = {"action": action}
    if action == "set_level":
        if tool_call.get("level_pct") is None:
            raise ValueError("set_level requires level_pct")
        payload["level_pct"] = tool_call["level_pct"]
    if tool_call.get("duration_s") is not None:
        payload["duration_s"] = tool_call["duration_s"]

    topic = f"devices/{device_id}/cmd"
    return _publish_mqtt(topic, payload)


def _publish_mqtt(topic: str, payload: dict) -> str:
    message = json.dumps(payload)
    broker = os.environ.get("MQTT_BROKER")
    if not broker:
        print(f"[dry run — set MQTT_BROKER to actually publish] {topic} -> {message}")
        return message

    try:
        import paho.mqtt.publish as mqtt_publish
    except ImportError:
        print(f"[paho-mqtt not installed — dry run] {topic} -> {message}")
        return message

    port = int(os.environ.get("MQTT_PORT", "1883"))
    mqtt_publish.single(topic, payload=message, hostname=broker, port=port)
    print(f"[published] {topic} -> {message}")
    return message


# ---------------------------------------------------------------------------
# 4. Wire it together
# ---------------------------------------------------------------------------

def handle_command(user_text: str) -> None:
    print(f"\n> {user_text}")
    tool_call = nl_to_tool_call(user_text)
    print(f"  tool_call = {tool_call}")
    try:
        dispatch(tool_call)
    except (UnknownDeviceError, ValueError) as e:
        print(f"  REJECTED by deterministic dispatcher: {e}")


if __name__ == "__main__":
    examples = [
        "turn on the greenhouse pump for 10 seconds",
        "set the greenhouse fan to 70%",
        "turn off the battery charger relay",
        "open the front gate",  # not a known device -> should be rejected
    ]
    commands = sys.argv[1:] or examples
    for cmd in commands:
        handle_command(cmd)
