import json

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.errors import DomainError
from app.platform import RunInput, result


def run_input():
    return RunInput(
        input_text="20分钟",
        conversation_id="c1",
        turn_id="t1",
        trace_id="trace1",
        current_time="2026-11-02T10:00:00+08:00",
        business_context={"vehicle_id": "V01"},
        trusted_task_context={"task_token": "secret-task"},
    )


def completion(content=None, calls=None):
    return {
        "choices": [
            {"message": {"role": "assistant", "content": content, "tool_calls": calls or []}}
        ]
    }


def tool(name="get_delivery_context", arguments=None):
    return {
        "id": "call1",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments or {"vehicle_id": "V01"})},
    }


def adapter(handler):
    from app.platform import DeepSeekPlatform

    return DeepSeekPlatform(
        Settings(agent_mode="deepseek", deepseek_api_key="private-key"),
        transport=httpx.MockTransport(handler),
    )


def test_direct_tools_then_strict_json_and_no_trusted_context():
    requests, calls = [], []

    class Gateway:
        def call(self, name, values):
            calls.append((name, values))
            return {"snapshot_id": "s1"}

    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        assert request.headers["authorization"] == "Bearer private-key"
        assert "secret-task" not in request.content.decode()
        assert "trusted_task_context" not in request.content.decode()
        assert payload["thinking"] == {"type": "disabled"}
        assert not any("strict" in t["function"] for t in payload.get("tools", []))
        if len(requests) == 1:
            return httpx.Response(200, json=completion(calls=[tool()]))
        assert payload["messages"][-1] == {
            "role": "tool",
            "tool_call_id": "call1",
            "content": '{"ok": true, "data": {"snapshot_id": "s1"}}',
        }
        return httpx.Response(200, json=completion(result().model_dump_json()))

    assert adapter(handle).invoke(run_input(), Gateway()).business_state == "NEEDS_INFO"
    assert calls == [("get_delivery_context", {"vehicle_id": "V01", "order_no": None})]
    assert requests[-1]["response_format"] == {"type": "json_object"}


@pytest.mark.parametrize(
    "arguments", [{"vehicle_id": "V01", "user_id": "forged"}, {"vehicle_id": "V01", "unknown": 1}]
)
def test_unknown_or_forged_tool_parameters_never_reach_gateway(arguments):
    seen = []

    class Gateway:
        def call(self, *args):
            seen.append(args)

    with pytest.raises((ValidationError, DomainError)):
        adapter(
            lambda req: httpx.Response(200, json=completion(calls=[tool(arguments=arguments)]))
        ).invoke(run_input(), Gateway())
    assert not seen


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, json={"error": "private"}),
        httpx.Response(200, json=completion("")),
        httpx.Response(200, json=completion('{"reply":"partial"}')),
    ],
)
def test_invalid_http_and_output_raise(response):
    with pytest.raises((httpx.HTTPError, ValueError, DomainError)):
        adapter(lambda req: response).invoke(run_input(), None)


def test_tool_loop_budget_stops_before_ninth_execution():
    seen = []

    class Gateway:
        def call(self, *args):
            seen.append(args)
            return {}

    with pytest.raises(DomainError, match="Eight tool"):
        adapter(lambda req: httpx.Response(200, json=completion(calls=[tool()]))).invoke(
            run_input(), Gateway()
        )
    assert len(seen) == 8


def test_total_deadline_includes_tools(monkeypatch):
    from app import platform

    ticks = iter([0.0, 0.0, 46.0])
    monkeypatch.setattr(platform, "monotonic", lambda: next(ticks))
    with pytest.raises(DomainError, match="budget"):
        adapter(
            lambda req: httpx.Response(200, json=completion(result().model_dump_json()))
        ).invoke(run_input(), None)


def test_agentarts_mode_is_rejected():
    with pytest.raises(ValidationError):
        Settings(agent_mode="agentarts")


def test_slow_http_response_is_cancelled_at_total_deadline():
    import asyncio
    import time

    from app.platform import DeepSeekPlatform

    class SlowTransport(httpx.BaseTransport, httpx.AsyncBaseTransport):
        def handle_request(self, request):
            time.sleep(1.3)
            return httpx.Response(200, json=completion(result().model_dump_json()))

        async def handle_async_request(self, request):
            await asyncio.sleep(1.3)
            return httpx.Response(200, json=completion(result().model_dump_json()))

    platform = DeepSeekPlatform(
        Settings(deepseek_api_key="private", platform_wait_seconds=1), transport=SlowTransport()
    )
    started = time.perf_counter()
    with pytest.raises(DomainError, match="budget"):
        platform.invoke(run_input(), None)
    assert time.perf_counter() - started < 1.2


def test_gateway_error_is_sent_as_error_not_success():
    requests = []

    class Gateway:
        def call(self, *args):
            raise DomainError("NOT_FOUND", "Object not found", 404)

    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            return httpx.Response(200, json=completion(calls=[tool()]))
        outcome = json.loads(payload["messages"][-1]["content"])
        assert outcome["ok"] is False and outcome["error"]["code"] == "NOT_FOUND"
        assert "data" not in outcome
        return httpx.Response(200, json=completion(result().model_dump_json()))

    adapter(handle).invoke(run_input(), Gateway())
