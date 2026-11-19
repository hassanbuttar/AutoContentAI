"""Claude client: one structured call per purpose, budget-gated, usage-tracked, output-validated.

Uses the official `anthropic` SDK. A transport abstraction lets tests inject a fake without network.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.core import secrets
from app.models import AIUsageRecord
from app.services.ai.budget import BudgetExceeded, check_budget, estimate_cost
from app.services.settings_service import get_section

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)
FALLBACK_BETA = "server-side-fallback-2026-07-01"
API_KEY_SECRET = "anthropic.api_key"


class AIUnavailable(Exception):
    """No key configured / AI disabled. Callers fall back to local planning."""


class AIRequestError(Exception):
    def __init__(self, message: str, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class LLMResponse:
    text: str
    model: str
    stop_reason: str | None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    request_id: str | None = None
    raw_stop_details: Any = None


class LLMTransport(Protocol):
    def __call__(self, request: dict[str, Any]) -> LLMResponse: ...


class AnthropicTransport:
    def __init__(self, api_key: str, use_fallbacks: bool = True, timeout: float = 300.0):
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=2)
        self.use_fallbacks = use_fallbacks

    def __call__(self, request: dict[str, Any]) -> LLMResponse:
        a = self._anthropic
        try:
            if self.use_fallbacks:
                msg = self.client.beta.messages.create(**request, betas=[FALLBACK_BETA], fallbacks="default")
            else:
                msg = self.client.messages.create(**request)
        except a.RateLimitError as e:
            raise AIRequestError(f"Claude rate limited: {e.message}", retryable=True) from e
        except a.AuthenticationError as e:
            raise AIRequestError("Claude API key is invalid (authentication failed)", retryable=False) from e
        except a.PermissionDeniedError as e:
            raise AIRequestError(f"Claude API permission denied: {e.message}", retryable=False) from e
        except a.NotFoundError as e:
            raise AIRequestError(f"Model or endpoint not found: {e.message}", retryable=False) from e
        except a.BadRequestError as e:
            raise AIRequestError(f"Claude rejected the request: {e.message}", retryable=False) from e
        except a.APIStatusError as e:
            raise AIRequestError(f"Claude API error {e.status_code}: {e.message}", retryable=e.status_code >= 500) from e
        except a.APIConnectionError as e:
            raise AIRequestError("Cannot reach the Claude API (network)", retryable=True) from e
        text = "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text")
        u = msg.usage
        return LLMResponse(text=text, model=msg.model, stop_reason=msg.stop_reason,
                           input_tokens=u.input_tokens or 0, output_tokens=u.output_tokens or 0,
                           cache_creation_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
                           cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
                           request_id=getattr(msg, "_request_id", None), raw_stop_details=getattr(msg, "stop_details", None))


# Tests can replace this factory: lambda db, cfg: FakeTransport(...)
transport_factory: Callable[[Session, dict], LLMTransport] | None = None


def get_transport(db: Session, cfg: dict) -> LLMTransport:
    if transport_factory is not None:
        return transport_factory(db, cfg)
    key = secrets.get_secret(db, API_KEY_SECRET)
    if not key:
        raise AIUnavailable("Claude API key not configured (Settings → AI)")
    return AnthropicTransport(key, use_fallbacks=bool(cfg.get("use_refusal_fallbacks", True)))


def ai_available(db: Session) -> tuple[bool, str]:
    cfg = get_section(db, "ai")
    if not cfg.get("enabled", True):
        return False, "AI disabled in settings"
    if transport_factory is None and not secrets.get_secret(db, API_KEY_SECRET):
        return False, "Claude API key not configured"
    return True, "ok"


@dataclass
class CallResult:
    data: Any
    usage: AIUsageRecord
    prompt_chars: int = 0
    extra: dict = field(default_factory=dict)


def structured_call(db: Session, *, purpose: str, model_cls: type[T], schema: dict, system_blocks: list[dict],
                    user_text: str, application_id: int | None, job_id: int | None,
                    max_tokens: int | None = None) -> CallResult:
    """Exactly one Claude request (plus SDK transport retries). Raises BudgetExceeded / AIUnavailable /
    AIRequestError. Successful or not, usage is recorded."""
    cfg = get_section(db, "ai")
    if not cfg.get("enabled", True):
        raise AIUnavailable("AI disabled in settings")
    transport = get_transport(db, cfg)
    max_tokens = int(max_tokens or cfg.get("max_output_tokens", 8000))
    prompt_chars = sum(len(b.get("text", "")) for b in system_blocks) + len(user_text)
    est = int(prompt_chars / 3.2) + max_tokens
    check_budget(db, application_id, job_id, est)  # raises BudgetExceeded
    request = {
        "model": cfg["model"],
        "max_tokens": max_tokens,
        "system": system_blocks,
        "messages": [{"role": "user", "content": user_text}],
        "output_config": {"effort": cfg.get("effort", "medium"), "format": {"type": "json_schema", "schema": schema}},
    }
    started = time.monotonic()
    rec = AIUsageRecord(application_id=application_id, job_id=job_id, purpose=purpose, model=cfg["model"])
    try:
        resp = transport(request)
    except AIRequestError as exc:
        rec.success, rec.error, rec.duration_ms = False, str(exc)[:2000], int((time.monotonic() - started) * 1000)
        db.add(rec)
        db.commit()
        raise
    rec.duration_ms = int((time.monotonic() - started) * 1000)
    rec.model = resp.model or cfg["model"]
    rec.input_tokens, rec.output_tokens = resp.input_tokens, resp.output_tokens
    rec.cache_creation_tokens, rec.cache_read_tokens = resp.cache_creation_tokens, resp.cache_read_tokens
    rec.request_id, rec.stop_reason = resp.request_id, resp.stop_reason
    rec.estimated_cost = estimate_cost(cfg.get("pricing", {}), rec.model, rec.input_tokens, rec.output_tokens,
                                       rec.cache_read_tokens, rec.cache_creation_tokens)
    db.add(rec)
    db.flush()
    try:
        if resp.stop_reason == "refusal":
            raise AIRequestError(f"Claude declined the request ({getattr(resp.raw_stop_details, 'category', None)})", retryable=False)
        if resp.stop_reason == "max_tokens":
            raise AIRequestError("Claude output was truncated (max_tokens); increase AI → max output tokens", retryable=False)
        try:
            data = json.loads(resp.text)
            parsed = model_cls.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise AIRequestError(f"Claude returned invalid structured output: {str(exc)[:400]}", retryable=True) from exc
    except AIRequestError as exc:
        rec.success, rec.error = False, str(exc)[:2000]
        db.commit()
        raise
    db.commit()
    log.info("Claude %s: in=%s out=%s cache_r=%s cache_w=%s cost=$%.4f", purpose, rec.input_tokens, rec.output_tokens,
             rec.cache_read_tokens, rec.cache_creation_tokens, rec.estimated_cost)
    return CallResult(data=parsed, usage=rec, prompt_chars=prompt_chars)


def system_with_profile(system_text: str, profile_text: str | None) -> list[dict]:
    """Frozen system prompt + per-app profile. cache_control on the last stable block."""
    blocks = [{"type": "text", "text": system_text}]
    if profile_text:
        blocks.append({"type": "text", "text": profile_text, "cache_control": {"type": "ephemeral"}})
    else:
        blocks[0]["cache_control"] = {"type": "ephemeral"}
    return blocks


def connectivity_test(db: Session) -> dict:
    cfg = get_section(db, "ai")
    key = secrets.get_secret(db, API_KEY_SECRET)
    if not key and transport_factory is None:
        return {"ok": False, "error": "No API key configured"}
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=key, timeout=30, max_retries=0)
        m = client.models.retrieve(cfg["model"])
        return {"ok": True, "model": m.id, "display_name": getattr(m, "display_name", m.id)}
    except Exception as exc:  # noqa: BLE001 - report any failure to the wizard
        msg = getattr(exc, "message", None) or str(exc)
        return {"ok": False, "error": f"{type(exc).__name__}: {msg}"[:300]}


__all__ = ["structured_call", "system_with_profile", "AIUnavailable", "AIRequestError", "BudgetExceeded", "ai_available",
           "LLMResponse", "connectivity_test"]
