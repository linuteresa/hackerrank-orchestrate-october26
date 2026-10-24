"""Claude call layer: one structured-output request, a content-addressed cache, a usage ledger.

* Credentials come only from the environment (``ANTHROPIC_API_KEY`` or any other source the
  Anthropic SDK resolves). Nothing is hardcoded or logged.
* Every call uses JSON-schema structured output (``output_config.format``) and the server-side
  refusal fallback (``fallbacks: "default"``). Output is validated again in code by the caller.
* Results are cached under ``code/cache/<purpose>/<sha256>.json`` keyed by model, prompt version
  and input digest, so reruns are deterministic and free. Each cache entry keeps the usage of the
  call that produced it, so a fully cached run can still report what its outputs cost.
* If no credentials are available, ``call`` raises ``ModelUnavailable`` once and the caller marks
  the affected decision unresolved. No result is ever invented.
"""

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
CACHE_DIR = Path(__file__).resolve().parents[1] / "cache"

# USD per million tokens (Anthropic first-party list price for claude-opus-5-5, 2026-10).
PRICING = {
    MODEL: {"input": 4.00, "output": 20.00, "cache_read": 0.20, "cache_write": 5.00},
}


class ModelUnavailable(RuntimeError):
    """No usable credentials / client: the decision that needed the model stays unresolved."""


class ModelRefused(RuntimeError):
    pass


def digest(*parts):
    h = hashlib.sha256()
    for part in parts:
        h.update(part if isinstance(part, bytes) else str(part).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


@dataclass
class Usage:
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def cost(self):
        price = PRICING.get(self.model)
        if not price:
            return None
        return (
            self.input_tokens * price["input"]
            + self.output_tokens * price["output"]
            + self.cache_read_input_tokens * price["cache_read"]
            + self.cache_creation_input_tokens * price["cache_write"]
        ) / 1_000_000


@dataclass
class Ledger:
    """Calls made (live) and outputs reused (cache) during one run, per purpose and model."""

    entries: list = field(default_factory=list)

    def record(self, purpose, source, usage):
        self.entries.append({"purpose": purpose, "source": source, **usage.__dict__})

    def summary(self):
        out = {}
        for entry in self.entries:
            key = (entry["purpose"], entry["model"], entry["source"])
            row = out.setdefault(key, {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                                       "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
                                       "cost_usd": 0.0})
            row["calls"] += 1
            for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
                row[k] += entry[k]
            usage = Usage(entry["model"], entry["input_tokens"], entry["output_tokens"],
                          entry["cache_read_input_tokens"], entry["cache_creation_input_tokens"])
            row["cost_usd"] += usage.cost() or 0.0
        return [{"purpose": p, "model": m, "source": s, **v} for (p, m, s), v in sorted(out.items())]


class Claude:
    def __init__(self, ledger=None, offline=False, cache_dir=CACHE_DIR, client=None):
        self.ledger = ledger or Ledger()
        self.offline = offline
        self.cache_dir = Path(cache_dir)
        self._client = client
        self._unavailable_reason = None

    def _get_client(self):
        if self._client is None:
            import anthropic  # imported lazily so offline runs need no SDK

            self._client = anthropic.Anthropic()
        return self._client

    def cache_path(self, purpose, key):
        return self.cache_dir / purpose / f"{key}.json"

    def call(self, purpose, prompt_version, input_digest, content, schema, effort="low", max_tokens=4096):
        """Return ``(data, source)``; ``source`` is ``"cache"`` or ``"live"``."""
        key = digest(MODEL, purpose, prompt_version, input_digest)
        path = self.cache_path(purpose, key)
        if path.exists():
            entry = json.loads(path.read_text(encoding="utf-8"))
            self.ledger.record(purpose, "cache", Usage(**entry["usage"]))
            return entry["output"], "cache"
        if self.offline:
            raise ModelUnavailable("offline mode")
        if self._unavailable_reason:
            raise ModelUnavailable(self._unavailable_reason)
        try:
            response = self._create(content, schema, effort, max_tokens)
        except ModelUnavailable as exc:
            self._unavailable_reason = str(exc)
            raise
        if response.stop_reason == "refusal":
            raise ModelRefused(str(getattr(response, "stop_details", None)))
        if response.stop_reason == "max_tokens":
            raise RuntimeError(f"{purpose}: output truncated at max_tokens")
        text = next(block.text for block in response.content if block.type == "text")
        output = json.loads(text)
        usage = Usage(
            model=getattr(response, "model", MODEL) or MODEL,
            input_tokens=response.usage.input_tokens or 0,
            output_tokens=response.usage.output_tokens or 0,
            cache_read_input_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
            cache_creation_input_tokens=getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
        )
        self.ledger.record(purpose, "live", usage)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "model": usage.model, "purpose": purpose, "prompt_version": prompt_version,
            "input_digest": input_digest, "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "usage": usage.__dict__, "output": output,
        }, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        return output, "live"

    def _create(self, content, schema, effort, max_tokens):
        import anthropic

        try:
            return self._get_client().beta.messages.create(
                model=MODEL,
                max_tokens=max_tokens,
                betas=[FALLBACK_BETA],
                fallbacks="default",
                output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
                messages=[{"role": "user", "content": content}],
            )
        except TypeError as exc:  # SDK: "Could not resolve authentication method"
            if "authentication" in str(exc).lower():
                raise ModelUnavailable("no Anthropic credentials in the environment") from exc
            raise
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise ModelUnavailable(f"credentials rejected: {type(exc).__name__}") from exc


def credentials_present():
    """Cheap hint only; the SDK may also resolve profiles. Never prints values."""
    return any(os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"))
