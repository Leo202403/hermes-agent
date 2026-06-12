import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PlatformConfig
from gateway.platforms import alphahunt_stage
from gateway.platforms.api_server import APIServerAdapter
from tests.gateway.test_api_server import _create_app


def _payload(mode: str) -> dict:
    payload = {
        "request_id": f"req-{mode}",
        "analysis_mode": mode,
        "context": {"routing_policy": {"preferred_engine": "qwen_local"}},
    }
    if mode == "fast_triage":
        payload["context"] = {}
    return payload


def _stage_output(mode: str) -> dict:
    outputs = {
        "cleaner": {
            "status": "ok",
            "normalized_event": {
                "event_id": "evt-1",
                "asset_class": "bond",
                "event_type": "auction",
                "source": "dry_run",
                "normalized_fields": {"identity_key": "UST-10Y"},
            },
            "data_gaps": [],
        },
        "screener": {
            "status": "ok",
            "opportunity_candidate": {
                "opportunity_id": "opp-1",
                "asset_class": "bond",
                "base_fields": {"source_event_id": "evt-1"},
                "asset_specific_fields": {"instrument": "UST-10Y"},
                "decision_fields": {"screening_decision": "candidate"},
            },
        },
        "sentinel": {
            "status": "ok",
            "risks": [],
            "risk_veto": {"active": False, "reason": "none"},
            "blocking_rules": [],
        },
        "packager": {
            "status": "ok",
            "context_packet": {
                "normalized_event": {
                    "event_id": "evt-1",
                    "asset_class": "bond",
                    "event_type": "auction",
                    "source": "dry_run",
                    "normalized_fields": {"identity_key": "UST-10Y"},
                },
                "opportunity_candidate": {
                    "opportunity_id": "opp-1",
                    "asset_class": "bond",
                    "base_fields": {"source_event_id": "evt-1"},
                    "asset_specific_fields": {"instrument": "UST-10Y"},
                    "decision_fields": {"screening_decision": "candidate"},
                },
                "risk_review": {
                    "risks": [],
                    "risk_veto": {"active": False, "reason": "none"},
                    "blocking_rules": [],
                },
            },
        },
        "fast_triage": {
            "status": "ok",
            "triage_decision": "advance",
            "reason": "dry run",
            "matched_signals": [],
            "data_gap": [],
        },
        "project_update_research": {
            "status": "ok",
            "update_markdown": "# Update\n\nSemantic review from supplied evidence only.",
            "update_yaml": {
                "changed_evidence": [
                    {
                        "source": "watchpoint_report",
                        "observation": "Revenue watchpoint breached.",
                        "impact": "The original demand assumption is weaker.",
                    }
                ],
                "thesis_delta": "Thesis weakens because a core observable moved against the assumption.",
                "risk_delta": ["Demand risk increased."],
                "invalidation_delta": [
                    "No full invalidation without a second confirming source."
                ],
                "observables_update": [
                    {
                        "name": "revenue_growth",
                        "previous_status": "on_track",
                        "current_status": "breached",
                        "semantic_assessment": "material adverse change",
                    }
                ],
                "next_check_at": "2026-06-19T00:00:00+00:00",
                "confidence": 0.62,
                "action_suggestion": "manual_review",
                "data_gap": [],
            },
        },
        "post_mortem_research": {
            "status": "ok",
            "post_mortem_markdown": "# Post-Mortem\n\nClosed-item error review.",
            "evolution_proposal": {
                "source_evolution": [
                    {
                        "proposal": "Downweight stale source snapshots.",
                        "rationale": "The losing thesis relied on stale source data.",
                    }
                ],
                "rule_evolution": [
                    {
                        "proposal": "Require explicit recency checks before green-lighting.",
                        "rationale": "The breach existed before the original note.",
                    }
                ],
                "threshold_evolution": [
                    {
                        "proposal": "Tighten watchpoint breach threshold for this asset class.",
                        "rationale": "Prior threshold fired too late.",
                    }
                ],
                "data_gap": [],
                "confidence": 0.55,
                "enforced": False,
            },
        },
    }
    return outputs[mode]


def _model_json(mode: str) -> str:
    return json.dumps({
        "output": {
            "stage": mode,
            "stage_schema_version": alphahunt_stage.SCHEMA_VERSION_BY_MODE[mode],
            "analyzer": (
                alphahunt_stage.CODEX_ANALYZER
                if mode in alphahunt_stage.CODEX_MODES
                else alphahunt_stage.QWEN_ANALYZER
            ),
            "model": alphahunt_stage.CODEX_MODEL
            if mode in alphahunt_stage.CODEX_MODES
            else alphahunt_stage.QWEN_MODEL,
            "stage_output": _stage_output(mode),
        }
    })


@pytest.mark.parametrize(
    "mode",
    [
        "cleaner",
        "screener",
        "sentinel",
        "packager",
        "fast_triage",
        "project_update_research",
        "post_mortem_research",
    ],
)
def test_alphahunt_stage_returns_strict_json_callback(monkeypatch, mode):
    if mode in alphahunt_stage.CODEX_MODES:
        monkeypatch.setattr(
            alphahunt_stage,
            "call_codex_spark",
            lambda *args, **kwargs: _model_json(mode),
        )
    else:
        monkeypatch.setattr(
            alphahunt_stage, "call_ollama", lambda *args, **kwargs: _model_json(mode)
        )

    result = alphahunt_stage.run_alphahunt_stage(_payload(mode))

    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    decoded = json.loads(encoded)
    assert decoded == result
    assert result["analysis_id"] == f"req-{mode}"
    assert result["output"]["stage"] == mode
    assert result["output"]["analyzer"] == (
        alphahunt_stage.CODEX_ANALYZER
        if mode in alphahunt_stage.CODEX_MODES
        else alphahunt_stage.QWEN_ANALYZER
    )
    assert result["output"]["model"] == (
        alphahunt_stage.CODEX_MODEL
        if mode in alphahunt_stage.CODEX_MODES
        else alphahunt_stage.QWEN_MODEL
    )
    assert result["output"]["stage_output"]["status"] == "ok"
    ok, err = alphahunt_stage.validate_output(mode, result)
    assert ok, err


def test_stage_prompt_examples_do_not_use_empty_objects():
    for mode in [
        "cleaner",
        "screener",
        "sentinel",
        "packager",
        "project_update_research",
        "post_mortem_research",
    ]:
        example = alphahunt_stage.expected_output_shape(mode)
        encoded = json.dumps(example, ensure_ascii=False, separators=(",", ":"))
        assert "{}" not in encoded
        ok, err = alphahunt_stage.validate_output(
            mode,
            {"analysis_id": f"req-{mode}", "output": example["output"]},
        )
        assert ok, err


def test_call_ollama_sends_larger_generation_options(monkeypatch):
    captured = {}

    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"response": '{"output":{}}'}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return DummyResponse()

    monkeypatch.setattr(alphahunt_stage.requests, "post", fake_post)

    result = alphahunt_stage.call_ollama("prompt", timeout=12)

    assert result == '{"output":{}}'
    assert captured["json"]["options"] == {"num_predict": 1024, "num_ctx": 4096}
    assert captured["timeout"] == 12


def test_call_ollama_ignores_invalid_numeric_env(monkeypatch):
    captured = {}

    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"response": '{"output":{}}'}

    def fake_post(url, json, timeout):
        captured["json"] = json
        captured["timeout"] = timeout
        return DummyResponse()

    monkeypatch.setenv("HERMES_QWEN_NUM_PREDICT", "bad")
    monkeypatch.setenv("HERMES_QWEN_NUM_CTX", "also-bad")
    monkeypatch.setattr(alphahunt_stage.requests, "post", fake_post)

    result = alphahunt_stage.call_ollama("prompt", timeout=12)

    assert result == '{"output":{}}'
    assert captured["json"]["options"] == {"num_predict": 1024, "num_ctx": 4096}
    assert captured["timeout"] == 12


def test_run_qwen_stage_ignores_invalid_timeout_env(monkeypatch):
    captured = {}

    def fake_call(prompt, *, timeout):
        captured["timeout"] = timeout
        return _model_json("fast_triage")

    monkeypatch.setenv("HERMES_QWEN_TIMEOUT_SEC", "bad")
    monkeypatch.setattr(alphahunt_stage, "call_ollama", fake_call)

    result = alphahunt_stage.run_qwen_stage(_payload("fast_triage"))

    assert result["output"]["stage_output"]["status"] == "ok"
    assert captured["timeout"] == 90


@pytest.mark.parametrize("decision", ["advance", "reject", "needs_human"])
def test_fast_triage_decisions_validate(monkeypatch, decision):
    def model_json(*args, **kwargs):
        body = json.loads(_model_json("fast_triage"))
        body["output"]["stage_output"]["triage_decision"] = decision
        body["output"]["stage_output"]["reason"] = f"{decision} path"
        return json.dumps(body)

    monkeypatch.setattr(alphahunt_stage, "call_ollama", model_json)

    result = alphahunt_stage.run_qwen_stage(_payload("fast_triage"))

    assert result["output"]["stage_output"]["status"] == "ok"
    assert result["output"]["stage_output"]["triage_decision"] == decision
    ok, err = alphahunt_stage.validate_output("fast_triage", result)
    assert ok, err


def test_is_alphahunt_stage_payload_matches_stage_and_fast_triage():
    assert alphahunt_stage.is_alphahunt_stage_payload(_payload("cleaner")) is True
    assert alphahunt_stage.is_alphahunt_stage_payload(_payload("fast_triage")) is True
    assert (
        alphahunt_stage.is_alphahunt_stage_payload(_payload("project_update_research"))
        is True
    )
    assert (
        alphahunt_stage.is_alphahunt_stage_payload(_payload("post_mortem_research"))
        is True
    )
    assert (
        alphahunt_stage.is_alphahunt_stage_payload({"analysis_mode": "rejudge"})
        is False
    )
    assert (
        alphahunt_stage.is_alphahunt_stage_payload({
            "analysis_mode": "cleaner",
            "context": {"routing_policy": {"preferred_engine": "codex_spark"}},
        })
        is True
    )
    assert (
        alphahunt_stage.is_alphahunt_stage_payload({
            "analysis_mode": "cleaner",
            "context": {"routing_policy": {"preferred_engine": "central"}},
        })
        is False
    )


@pytest.mark.asyncio
async def test_analysis_endpoint_dispatches_alphahunt_stage_and_callback(monkeypatch):
    captured = {}

    def fake_run(payload):
        return {
            "analysis_id": payload["request_id"],
            "output": {
                "stage": "fast_triage",
                "stage_schema_version": "fast_triage_v1",
                "analyzer": "qwen_7b_local",
                "model": "qwen2.5:7b",
                "stage_output": _stage_output("fast_triage"),
            },
        }

    def fake_post(payload, callback, *, callback_url="", callback_auth=""):
        captured["payload"] = payload
        captured["callback"] = callback
        captured["callback_url"] = callback_url
        captured["callback_auth"] = callback_auth

    from gateway.platforms import api_server as api_server_mod

    monkeypatch.setattr(api_server_mod, "run_alphahunt_stage", fake_run)
    monkeypatch.setattr(api_server_mod, "_post_alphahunt_stage_callback", fake_post)

    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={"key": "sk-test"}))
    adapter._run_agent = AsyncMock()
    app = _create_app(adapter)
    app.router.add_post("/v1/analysis", adapter._handle_alphahunt_analysis)

    async with TestClient(TestServer(app)) as cli:
        resp = await cli.post(
            "/v1/analysis",
            headers={
                "Authorization": "Bearer sk-test",
                "X-AlphaHunt-Callback-URL": "http://central/callback",
            },
            json=_payload("fast_triage"),
        )
        body = await resp.json()

    assert resp.status == 200
    assert body["accepted"] is True
    assert body["analysis_id"] == "req-fast_triage"
    assert body["output"]["stage"] == "fast_triage"
    assert (
        captured["callback"]["output"]["stage_output"]["triage_decision"] == "advance"
    )
    assert captured["callback_url"] == "http://central/callback"


@pytest.mark.asyncio
async def test_analysis_endpoint_accepts_codex_spark_stage_payload(monkeypatch):
    captured = {}

    def fake_run(payload):
        return {
            "analysis_id": payload["request_id"],
            "output": {
                "stage": "cleaner",
                "stage_schema_version": "cleaner_output_v1",
                "analyzer": alphahunt_stage.CODEX_ANALYZER,
                "model": alphahunt_stage.CODEX_MODEL,
                "stage_output": _stage_output("cleaner"),
            },
        }

    def fake_post(payload, callback, *, callback_url="", callback_auth=""):
        captured["payload"] = payload
        captured["callback"] = callback

    from gateway.platforms import api_server as api_server_mod

    monkeypatch.setattr(api_server_mod, "run_alphahunt_stage", fake_run)
    monkeypatch.setattr(api_server_mod, "_post_alphahunt_stage_callback", fake_post)

    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={"key": "sk-test"}))
    adapter._run_agent = AsyncMock()
    app = _create_app(adapter)
    app.router.add_post("/v1/analysis", adapter._handle_alphahunt_analysis)

    payload = _payload("cleaner")
    payload["context"]["routing_policy"]["preferred_engine"] = "codex_spark"
    async with TestClient(TestServer(app)) as cli:
        resp = await cli.post(
            "/v1/analysis",
            headers={"Authorization": "Bearer sk-test"},
            json=payload,
        )
        body = await resp.json()

    assert resp.status == 200
    assert body["accepted"] is True
    assert body["output"]["stage"] == "cleaner"
    assert body["output"]["analyzer"] == alphahunt_stage.CODEX_ANALYZER
    assert (
        captured["payload"]["context"]["routing_policy"]["preferred_engine"]
        == "codex_spark"
    )
    assert captured["callback"]["output"]["stage_output"]["status"] == "ok"
