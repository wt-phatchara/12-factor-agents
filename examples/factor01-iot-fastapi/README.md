# Factor 1 example: Natural Language to Tool Calls (IoT/MQTT)

A runnable example applying [Factor 1](../../content/factor-01-natural-language-to-tool-calls.md)
to IoT device control: a natural-language command is converted to a strict,
validated tool call, then a deterministic dispatcher (not the LLM) decides
whether to publish it to an MQTT device topic.

This is example code, not part of the 12-factor-agents library itself — the
repo is a set of principles, not a framework to import.

## Files

- `nl_to_mqtt.py` — the core pipeline: NL -> tool call -> deterministic MQTT
  dispatch. Runs standalone with no dependencies beyond the stdlib in mock
  mode (no `ANTHROPIC_API_KEY` needed); uses the real Claude API when a key
  is set.
- `app.py` — a FastAPI wrapper exposing the same pipeline over HTTP
  (`GET /devices`, `POST /command`).

## Run the pipeline directly

```bash
python3 nl_to_mqtt.py
python3 nl_to_mqtt.py "dim the greenhouse fan to 30 percent"
```

## Run the FastAPI server

```bash
pip install fastapi uvicorn "pydantic>=2"
uvicorn app:app --reload --port 8000
```

```bash
curl -X POST localhost:8000/command \
    -H "Content-Type: application/json" \
    -d '{"text": "turn on the greenhouse pump for 10 seconds"}'
```

Interactive docs: http://localhost:8000/docs

## Use the real Claude API

```bash
pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...
```

## Publish to a real MQTT broker

```bash
pip install paho-mqtt
export MQTT_BROKER=192.168.1.50
```

Without `MQTT_BROKER` set, commands run in dry-run mode and just print the
payload that would have been published.
