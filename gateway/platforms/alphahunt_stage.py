"""AlphaHunt local-Qwen analysis stage handler."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

STAGE_MODES = frozenset({"cleaner", "screener", "sentinel", "packager"})
RESEARCH_MODES = frozenset({"project_update_research", "post_mortem_research"})
CODEX_MODES = STAGE_MODES | RESEARCH_MODES
QWEN_MODES = CODEX_MODES | frozenset({"fast_triage"})
QWEN_ANALYZER = "qwen_7b_local"
QWEN_MODEL = "qwen2.5:7b"
CODEX_ANALYZER = "codex_spark"
CODEX_MODEL = (
    os.environ.get("HERMES_CODEX_SPARK_MODEL", "gpt-5.3-codex-spark").strip()
    or "gpt-5.3-codex-spark"
)
DEFAULT_CODEX_BIN = "/home/leo/.hermes/node/bin/codex"
DEFAULT_CODEX_TIMEOUT_SEC = 180
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
DEFAULT_QWEN_TIMEOUT_SEC = 90
DEFAULT_QWEN_NUM_PREDICT = 1024
DEFAULT_QWEN_NUM_CTX = 4096

PROMPTS: Dict[str, str] = {
    "cleaner": (
        "你只负责把 raw_snapshot 清洗成 normalized_event。保留 source、asset_class、"
        "identity_key、action_window、关键字段与显式 data gap。禁止给投资结论，禁止补脑补字段。"
    ),
    "screener": (
        "你只负责把 normalized_event 转成 opportunity_candidate。必须拆成 base_fields、"
        "asset_specific_fields、decision_fields，不得越权输出 green/act_now。"
    ),
    "sentinel": (
        "你只负责阻断。检查 Web3、A股/ETF、港股打新、美债、黄金的 blocking rules。"
        "risk_veto.active=true 时必须阻断 green/act_now。"
    ),
    "packager": (
        "你是 Agent 4 打包员。输入是前三段产出（cleaner_output_v1、screener_output_v1、"
        "sentinel_output_v1）。你只做拼装：把三段事实层结论合成 hermes_decision_object_v2 "
        "解释层需要的 context_packet（normalized_event + opportunity_candidate? + risk_review）。"
        "禁止新增事实、禁止给 action_color/recommended_action、禁止覆盖 sentinel 的 blocking risk。"
        "输出必须严格符合 packager_output_v1。"
    ),
    "fast_triage": (
        "你是一过筛 (fast_triage) 员。输入是 raw_snapshot 或 normalized_event。你只判断该事件"
        "是否有进入 4-stage agent chain 的价值。输出严格 JSON。"
    ),
    "project_update_research": (
        "你是 AlphaHunt project update researcher。输入包含 previous thesis、watchpoints、"
        "latest source data、data_gap、breach/recheck_due 和/或 Hermes update research task/artifact。"
        "你必须做语义级复查：判断证据变化是否真的改变 thesis、risk、invalidation、observables，"
        "不要只做 rule_v1 机械映射。缺数据必须进入 data_gap，禁止补造数值或来源。"
        "输出 update_markdown 与 update_yaml。update_yaml 必须包含 changed_evidence、thesis_delta、"
        "risk_delta、invalidation_delta、observables_update、next_check_at、confidence、action_suggestion。"
        "只做研究和记录建议；不交易、不下注、不发通知、不调用外部接口。"
    ),
    "post_mortem_research": (
        "你是 AlphaHunt post-mortem researcher。输入是已结案 opportunity/project、previous thesis、"
        "最终结果、触发/未触发 watchpoints、数据缺口和已有 post_mortem notes。"
        "你必须总结错因，并提出 source/rule/threshold evolution proposal。"
        "缺数据必须进入 data_gap；proposal 只能是 enforced=false 的研究建议，禁止自动执行、交易、下注、通知或写库。"
    ),
}

SCHEMA_VERSION_BY_MODE = {
    "cleaner": "cleaner_output_v1",
    "screener": "screener_output_v1",
    "sentinel": "sentinel_output_v1",
    "packager": "packager_output_v1",
    "fast_triage": "fast_triage_v1",
    "project_update_research": "hermes_update_research_v1",
    "post_mortem_research": "post_mortem_research_v1",
}

OUTPUT_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "cleaner": {
        "type": "object",
        "required": ["status"],
        "properties": {
            "status": {"enum": ["ok", "rejected", "error"]},
            "reason": {"type": "string"},
            "normalized_event": {
                "type": "object",
                "required": [
                    "event_id",
                    "asset_class",
                    "event_type",
                    "source",
                    "normalized_fields",
                ],
                "properties": {
                    "event_id": {"type": "string", "minLength": 1},
                    "asset_class": {"type": "string", "minLength": 1},
                    "event_type": {"type": "string", "minLength": 1},
                    "source": {"type": "string", "minLength": 1},
                    "normalized_fields": {"type": "object", "minProperties": 1},
                },
            },
            "data_gaps": {"type": "array"},
        },
        "allOf": [
            {
                "if": {"properties": {"status": {"const": "ok"}}},
                "then": {"required": ["normalized_event", "data_gaps"]},
            }
        ],
    },
    "screener": {
        "type": "object",
        "required": ["status"],
        "properties": {
            "status": {"enum": ["ok", "rejected", "error"]},
            "reason": {"type": "string"},
            "opportunity_candidate": {
                "type": "object",
                "required": [
                    "opportunity_id",
                    "asset_class",
                    "base_fields",
                    "asset_specific_fields",
                    "decision_fields",
                ],
                "properties": {
                    "opportunity_id": {"type": "string", "minLength": 1},
                    "asset_class": {"type": "string", "minLength": 1},
                    "base_fields": {"type": "object", "minProperties": 1},
                    "asset_specific_fields": {"type": "object", "minProperties": 1},
                    "decision_fields": {"type": "object", "minProperties": 1},
                },
            },
        },
        "allOf": [
            {
                "if": {"properties": {"status": {"const": "ok"}}},
                "then": {"required": ["opportunity_candidate"]},
            }
        ],
    },
    "sentinel": {
        "type": "object",
        "required": ["status"],
        "properties": {
            "status": {"enum": ["ok", "rejected", "error"]},
            "reason": {"type": "string"},
            "risks": {"type": "array"},
            "risk_veto": {
                "type": "object",
                "required": ["active"],
                "properties": {
                    "active": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
            },
            "blocking_rules": {"type": "array"},
        },
        "allOf": [
            {
                "if": {"properties": {"status": {"const": "ok"}}},
                "then": {"required": ["risks", "risk_veto"]},
            }
        ],
    },
    "packager": {
        "type": "object",
        "required": ["status"],
        "properties": {
            "status": {"enum": ["ok", "rejected", "error"]},
            "reason": {"type": "string"},
            "context_packet": {
                "type": "object",
                "required": ["normalized_event", "risk_review"],
                "properties": {
                    "normalized_event": {
                        "type": "object",
                        "required": [
                            "event_id",
                            "asset_class",
                            "event_type",
                            "source",
                            "normalized_fields",
                        ],
                        "properties": {
                            "event_id": {"type": "string", "minLength": 1},
                            "asset_class": {"type": "string", "minLength": 1},
                            "event_type": {"type": "string", "minLength": 1},
                            "source": {"type": "string", "minLength": 1},
                            "normalized_fields": {"type": "object", "minProperties": 1},
                        },
                    },
                    "opportunity_candidate": {
                        "type": "object",
                        "required": [
                            "opportunity_id",
                            "asset_class",
                            "base_fields",
                            "asset_specific_fields",
                            "decision_fields",
                        ],
                        "properties": {
                            "opportunity_id": {"type": "string", "minLength": 1},
                            "asset_class": {"type": "string", "minLength": 1},
                            "base_fields": {"type": "object", "minProperties": 1},
                            "asset_specific_fields": {
                                "type": "object",
                                "minProperties": 1,
                            },
                            "decision_fields": {"type": "object", "minProperties": 1},
                        },
                    },
                    "risk_review": {
                        "type": "object",
                        "required": ["risks", "risk_veto"],
                        "properties": {
                            "risks": {"type": "array"},
                            "risk_veto": {
                                "type": "object",
                                "required": ["active"],
                                "properties": {
                                    "active": {"type": "boolean"},
                                    "reason": {"type": "string"},
                                },
                            },
                            "blocking_rules": {"type": "array"},
                        },
                    },
                },
            },
        },
        "allOf": [
            {
                "if": {"properties": {"status": {"const": "ok"}}},
                "then": {"required": ["context_packet"]},
            }
        ],
    },
    "fast_triage": {
        "type": "object",
        "required": [
            "status",
            "triage_decision",
            "reason",
            "matched_signals",
            "data_gap",
        ],
        "properties": {
            "status": {"const": "ok"},
            "triage_decision": {"enum": ["advance", "reject", "needs_human"]},
            "reason": {"type": "string"},
            "matched_signals": {"type": "array", "items": {"type": "string"}},
            "data_gap": {"type": "array"},
        },
    },
    "project_update_research": {
        "type": "object",
        "required": ["status"],
        "properties": {
            "status": {"enum": ["ok", "rejected", "error"]},
            "reason": {"type": "string"},
            "update_markdown": {"type": "string", "minLength": 1},
            "update_yaml": {
                "type": "object",
                "required": [
                    "changed_evidence",
                    "thesis_delta",
                    "risk_delta",
                    "invalidation_delta",
                    "observables_update",
                    "next_check_at",
                    "confidence",
                    "action_suggestion",
                    "data_gap",
                ],
                "properties": {
                    "changed_evidence": {"type": "array", "items": {"type": "object"}},
                    "thesis_delta": {"type": "string", "minLength": 1},
                    "risk_delta": {"type": "array", "items": {"type": "string"}},
                    "invalidation_delta": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "observables_update": {
                        "type": "array",
                        "items": {"type": "object"},
                    },
                    "next_check_at": {"type": "string", "minLength": 1},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "action_suggestion": {
                        "enum": [
                            "no_change",
                            "observe",
                            "research",
                            "manual_review",
                            "deprioritize",
                            "archive",
                        ]
                    },
                    "data_gap": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "allOf": [
            {
                "if": {"properties": {"status": {"const": "ok"}}},
                "then": {"required": ["update_markdown", "update_yaml"]},
            }
        ],
    },
    "post_mortem_research": {
        "type": "object",
        "required": ["status"],
        "properties": {
            "status": {"enum": ["ok", "rejected", "error"]},
            "reason": {"type": "string"},
            "post_mortem_markdown": {"type": "string", "minLength": 1},
            "evolution_proposal": {
                "type": "object",
                "required": [
                    "source_evolution",
                    "rule_evolution",
                    "threshold_evolution",
                    "data_gap",
                    "confidence",
                    "enforced",
                ],
                "properties": {
                    "source_evolution": {"type": "array", "items": {"type": "object"}},
                    "rule_evolution": {"type": "array", "items": {"type": "object"}},
                    "threshold_evolution": {
                        "type": "array",
                        "items": {"type": "object"},
                    },
                    "data_gap": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "enforced": {"const": False},
                },
            },
        },
        "allOf": [
            {
                "if": {"properties": {"status": {"const": "ok"}}},
                "then": {"required": ["post_mortem_markdown", "evolution_proposal"]},
            }
        ],
    },
}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("Invalid %s=%r; using default %s", name, raw, default)
        return default


def is_alphahunt_stage_payload(payload: Dict[str, Any]) -> bool:
    mode = str(payload.get("analysis_mode") or "").strip().lower()
    if mode == "fast_triage":
        return True
    if mode not in CODEX_MODES:
        return False
    ctx = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    routing = (
        ctx.get("routing_policy") if isinstance(ctx.get("routing_policy"), dict) else {}
    )
    engine = str(routing.get("preferred_engine") or "").strip().lower()
    return (
        not engine
        or engine
        in {"qwen_local", "qwen_7b_local", CODEX_ANALYZER, "codex", "codex_local"}
        or engine.startswith("qwen")
        or engine.startswith("codex")
    )


def is_qwen_analysis_payload(payload: Dict[str, Any]) -> bool:
    """Backward-compatible alias for the AlphaHunt analysis dispatcher."""
    return is_alphahunt_stage_payload(payload)


def _analyzer_for_mode(mode: str) -> str:
    return CODEX_ANALYZER if mode in CODEX_MODES else QWEN_ANALYZER


def _model_for_mode(mode: str) -> str:
    return CODEX_MODEL if mode in CODEX_MODES else QWEN_MODEL


def build_prompt(payload: Dict[str, Any], *, validation_error: str = "") -> str:
    mode = str(payload.get("analysis_mode") or "").strip().lower()
    example = expected_output_shape(mode)
    analyzer = _analyzer_for_mode(mode)
    model = _model_for_mode(mode)
    repair = ""
    if validation_error:
        repair = f"\n上一次输出未通过 schema 校验：{validation_error}\n只返回修复后的严格 JSON。"
    return (
        f"{PROMPTS[mode]}\n"
        "硬性要求：只输出一个 JSON object，不要 Markdown，不要解释文字，不要代码块。\n"
        "顶层必须包含 output；output 必须包含 stage、stage_schema_version、analyzer、model、stage_output。\n"
        f"stage 必须是 {mode}；analyzer 必须是 {analyzer}；model 必须是 {model}。\n"
        f"stage_schema_version 必须是 {SCHEMA_VERSION_BY_MODE[mode]}。\n"
        f"期望形状示例：{json.dumps(example, ensure_ascii=False, separators=(',', ':'))}\n"
        f"{repair}\n"
        f"输入 payload：{json.dumps(payload, ensure_ascii=False, sort_keys=True)}"
    )


def expected_output_shape(mode: str) -> Dict[str, Any]:
    examples = {
        "cleaner": {
            "status": "ok",
            "normalized_event": {
                "event_id": "<event id>",
                "asset_class": "<asset class>",
                "event_type": "<event type>",
                "source": "<source>",
                "normalized_fields": {"identity_key": "<identity key>"},
            },
            "data_gaps": [],
        },
        "screener": {
            "status": "ok",
            "opportunity_candidate": {
                "opportunity_id": "<opportunity id>",
                "asset_class": "<asset class>",
                "base_fields": {"source_event_id": "<event id>"},
                "asset_specific_fields": {"instrument": "<instrument>"},
                "decision_fields": {
                    "screening_decision": "<candidate|reject|needs_human>"
                },
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
                    "event_id": "<event id>",
                    "asset_class": "<asset class>",
                    "event_type": "<event type>",
                    "source": "<source>",
                    "normalized_fields": {"identity_key": "<identity key>"},
                },
                "opportunity_candidate": {
                    "opportunity_id": "<opportunity id>",
                    "asset_class": "<asset class>",
                    "base_fields": {"source_event_id": "<event id>"},
                    "asset_specific_fields": {"instrument": "<instrument>"},
                    "decision_fields": {
                        "screening_decision": "<candidate|reject|needs_human>"
                    },
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
            "reason": "<one sentence>",
            "matched_signals": [],
            "data_gap": [],
        },
        "project_update_research": {
            "status": "ok",
            "update_markdown": "# Project Update\n\nSemantic review based on supplied evidence only.",
            "update_yaml": {
                "changed_evidence": [
                    {
                        "source": "<source name or URL>",
                        "observation": "<what changed>",
                        "impact": "<why it matters>",
                    }
                ],
                "thesis_delta": "No thesis change unless supplied evidence changes a core assumption.",
                "risk_delta": ["<risk change or no material change>"],
                "invalidation_delta": ["<invalidation change or no material change>"],
                "observables_update": [
                    {
                        "name": "<observable>",
                        "previous_status": "<prior status>",
                        "current_status": "<current status>",
                        "semantic_assessment": "<meaning>",
                    }
                ],
                "next_check_at": "<ISO8601>",
                "confidence": 0.5,
                "action_suggestion": "manual_review",
                "data_gap": [],
            },
        },
        "post_mortem_research": {
            "status": "ok",
            "post_mortem_markdown": "# Post-Mortem Research\n\nClosed-item error review based on supplied evidence only.",
            "evolution_proposal": {
                "source_evolution": [
                    {
                        "proposal": "<source reliability or coverage adjustment>",
                        "rationale": "<observed error pattern>",
                    }
                ],
                "rule_evolution": [
                    {
                        "proposal": "<rule adjustment>",
                        "rationale": "<why this would have helped>",
                    }
                ],
                "threshold_evolution": [
                    {
                        "proposal": "<threshold adjustment>",
                        "rationale": "<observed miss/false-positive pattern>",
                    }
                ],
                "data_gap": [],
                "confidence": 0.5,
                "enforced": False,
            },
        },
    }
    return {
        "output": {
            "stage": mode,
            "stage_schema_version": SCHEMA_VERSION_BY_MODE[mode],
            "analyzer": _analyzer_for_mode(mode),
            "model": _model_for_mode(mode),
            "stage_output": examples[mode],
        }
    }


def call_ollama(prompt: str, *, timeout: int = DEFAULT_QWEN_TIMEOUT_SEC) -> str:
    url = (
        os.environ.get("HERMES_QWEN_OLLAMA_URL", DEFAULT_OLLAMA_URL).strip()
        or DEFAULT_OLLAMA_URL
    )
    model = os.environ.get("HERMES_QWEN_MODEL", QWEN_MODEL).strip() or QWEN_MODEL
    num_predict = _env_int("HERMES_QWEN_NUM_PREDICT", DEFAULT_QWEN_NUM_PREDICT)
    num_ctx = _env_int("HERMES_QWEN_NUM_CTX", DEFAULT_QWEN_NUM_CTX)
    resp = requests.post(
        url,
        json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"num_predict": num_predict, "num_ctx": num_ctx},
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    return str(data.get("response") or "")


def call_codex_spark(prompt: str, *, timeout: int = DEFAULT_CODEX_TIMEOUT_SEC) -> str:
    codex_bin = (
        os.environ.get("HERMES_CODEX_BIN", DEFAULT_CODEX_BIN).strip()
        or DEFAULT_CODEX_BIN
    )
    workdir = os.environ.get("HERMES_CODEX_WORKDIR", os.getcwd()).strip() or os.getcwd()
    request_timeout = max(
        10,
        min(timeout, _env_int("HERMES_CODEX_TIMEOUT_SEC", DEFAULT_CODEX_TIMEOUT_SEC)),
    )
    with tempfile.NamedTemporaryFile(
        "w+", encoding="utf-8", suffix=".txt", delete=False
    ) as out_file:
        output_path = out_file.name
    cmd = [
        codex_bin,
        "exec",
        "-m",
        CODEX_MODEL,
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "-C",
        workdir,
        "--output-last-message",
        output_path,
        "-",
    ]
    try:
        completed = subprocess.run(
            cmd,
            input=prompt,
            cwd=workdir,
            text=True,
            capture_output=True,
            timeout=request_timeout,
            check=False,
        )
        try:
            output_text = Path(output_path).read_text(encoding="utf-8").strip()
        except Exception:
            output_text = ""
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"codex_spark_timeout:{request_timeout}s") from exc
    finally:
        try:
            Path(output_path).unlink(missing_ok=True)
        except Exception:
            pass
    if completed.returncode != 0:
        detail = (
            completed.stderr or completed.stdout or "Codex Spark analysis failed"
        ).strip()
        raise RuntimeError(detail[-2000:])
    text = output_text or (completed.stdout or "").strip()
    if not text:
        raise RuntimeError("codex_spark_empty_output")
    return text


def parse_json_object(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        obj = json.loads(match.group(0))
    if not isinstance(obj, dict):
        raise ValueError("model output must be a JSON object")
    return obj


def validate_output(mode: str, callback: Dict[str, Any]) -> Tuple[bool, str]:
    output = (
        callback.get("output") if isinstance(callback.get("output"), dict) else None
    )
    if output is None:
        return False, "missing output object"
    expected = {
        "stage": mode,
        "stage_schema_version": SCHEMA_VERSION_BY_MODE[mode],
        "analyzer": _analyzer_for_mode(mode),
        "model": _model_for_mode(mode),
    }
    for key, value in expected.items():
        if output.get(key) != value:
            return False, f"output.{key} expected {value!r}"
    stage_output = output.get("stage_output")
    if not isinstance(stage_output, dict):
        return False, "output.stage_output must be object"
    try:
        from jsonschema import Draft202012Validator

        errors = sorted(
            Draft202012Validator(OUTPUT_SCHEMAS[mode]).iter_errors(stage_output),
            key=lambda e: e.path,
        )
        if errors:
            return False, errors[0].message
    except ImportError:
        return _validate_stage_output_minimal(mode, stage_output)
    return True, ""


def _validate_stage_output_minimal(
    mode: str, stage_output: Dict[str, Any]
) -> Tuple[bool, str]:
    status = stage_output.get("status")
    if mode == "fast_triage":
        if status != "ok":
            return False, "status must be ok"
        if stage_output.get("triage_decision") not in {
            "advance",
            "reject",
            "needs_human",
        }:
            return False, "invalid triage_decision"
        for key in ("reason", "matched_signals", "data_gap"):
            if key not in stage_output:
                return False, f"{key} is required"
        return True, ""
    if status not in {"ok", "rejected", "error"}:
        return False, "invalid status"
    if status != "ok":
        return True, ""
    required_by_mode = {
        "cleaner": ("normalized_event", "data_gaps"),
        "screener": ("opportunity_candidate",),
        "sentinel": ("risks", "risk_veto"),
        "packager": ("context_packet",),
        "project_update_research": ("update_markdown", "update_yaml"),
        "post_mortem_research": ("post_mortem_markdown", "evolution_proposal"),
    }
    for key in required_by_mode[mode]:
        if key not in stage_output:
            return False, f"{key} is required"
    if mode == "project_update_research":
        update_yaml = stage_output.get("update_yaml")
        if not isinstance(update_yaml, dict):
            return False, "update_yaml is required"
        for key in (
            "changed_evidence",
            "thesis_delta",
            "risk_delta",
            "invalidation_delta",
            "observables_update",
            "next_check_at",
            "confidence",
            "action_suggestion",
            "data_gap",
        ):
            if key not in update_yaml:
                return False, f"update_yaml.{key} is required"
    if mode == "post_mortem_research":
        proposal = stage_output.get("evolution_proposal")
        if not isinstance(proposal, dict):
            return False, "evolution_proposal is required"
        if proposal.get("enforced") is not False:
            return False, "evolution_proposal.enforced must be false"
    return True, ""


def normalize_callback(
    payload: Dict[str, Any], model_obj: Dict[str, Any]
) -> Dict[str, Any]:
    mode = str(payload.get("analysis_mode") or "").strip().lower()
    analysis_id = str(
        payload.get("analysis_id") or payload.get("request_id") or ""
    ).strip()
    output = (
        model_obj.get("output") if isinstance(model_obj.get("output"), dict) else None
    )
    if output is None:
        output = {"stage_output": model_obj}
    output = dict(output)
    output["stage"] = mode
    output["stage_schema_version"] = SCHEMA_VERSION_BY_MODE[mode]
    output["analyzer"] = _analyzer_for_mode(mode)
    output["model"] = _model_for_mode(mode)
    if not isinstance(output.get("stage_output"), dict):
        output["stage_output"] = {"status": "error", "reason": "missing_stage_output"}
    result = {
        "analysis_id": analysis_id,
        "output": output,
    }
    event_id = str(payload.get("event_id") or "").strip()
    if event_id:
        result["event_id"] = event_id
    return result


def error_callback(payload: Dict[str, Any], mode: str, reason: str) -> Dict[str, Any]:
    analysis_id = str(
        payload.get("analysis_id") or payload.get("request_id") or ""
    ).strip()
    result: Dict[str, Any] = {
        "analysis_id": analysis_id,
        "output": {
            "stage": mode,
            "stage_schema_version": SCHEMA_VERSION_BY_MODE[mode],
            "analyzer": _analyzer_for_mode(mode),
            "model": _model_for_mode(mode),
            "stage_output": {"status": "error", "reason": reason},
        },
    }
    event_id = str(payload.get("event_id") or "").strip()
    if event_id:
        result["event_id"] = event_id
    return result


def run_alphahunt_stage(payload: Dict[str, Any]) -> Dict[str, Any]:
    mode = str(payload.get("analysis_mode") or "").strip().lower()
    if mode not in QWEN_MODES:
        raise ValueError(f"unsupported alphahunt analysis_mode: {mode}")
    timeout = (
        _env_int("HERMES_CODEX_TIMEOUT_SEC", DEFAULT_CODEX_TIMEOUT_SEC)
        if mode in CODEX_MODES
        else _env_int("HERMES_QWEN_TIMEOUT_SEC", DEFAULT_QWEN_TIMEOUT_SEC)
    )
    validation_error = ""
    for attempt in range(2):
        try:
            prompt = build_prompt(payload, validation_error=validation_error)
            raw = (
                call_codex_spark(prompt, timeout=timeout)
                if mode in CODEX_MODES
                else call_ollama(prompt, timeout=timeout)
            )
            model_obj = parse_json_object(raw)
            callback = normalize_callback(payload, model_obj)
        except requests.Timeout:
            return error_callback(payload, mode, "qwen_timeout")
        except Exception as exc:
            validation_error = f"invalid_json:{exc}"
            if attempt == 0:
                continue
            return error_callback(payload, mode, f"schema_invalid:{validation_error}")
        ok, err = validate_output(mode, callback)
        if ok:
            return callback
        validation_error = err
    return error_callback(payload, mode, f"schema_invalid:{validation_error}")


def run_qwen_stage(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Backward-compatible entrypoint; stage modes now route to Codex Spark."""
    return run_alphahunt_stage(payload)


def callback_headers(body: bytes, callback_auth: str = "") -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    api_key = (
        os.environ.get("CENTRAL_CALLBACK_API_KEY", "").strip() or callback_auth.strip()
    )
    if api_key:
        headers["X-API-Key"] = api_key
        headers["Authorization"] = f"Bearer {api_key}"
    secret = (
        os.environ.get("CENTRAL_CALLBACK_SECRET", "").strip()
        or os.environ.get("HERMES_CALLBACK_SECRET", "").strip()
        or os.environ.get("HERMES_DISPATCH_SECRET", "").strip()
    )
    if secret:
        ts = str(int(time.time()))
        msg = f"{ts}.".encode("utf-8") + body
        sig = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
        headers["X-Hermes-Timestamp"] = ts
        headers["X-Hermes-Signature"] = f"sha256={sig}"
    return headers


def post_callback(
    payload: Dict[str, Any],
    callback: Dict[str, Any],
    *,
    callback_url: str = "",
    callback_auth: str = "",
) -> None:
    url = (
        callback_url
        or payload.get("callback_url")
        or os.environ.get("CENTRAL_CALLBACK_URL")
        or ""
    ).strip()
    if not url:
        return
    auth = callback_auth or str(payload.get("callback_auth") or "")
    body = json.dumps(callback, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    resp = requests.post(
        url, data=body, headers=callback_headers(body, auth), timeout=15
    )
    resp.raise_for_status()
