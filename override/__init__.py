"""Kimi / Moonshot provider profiles (chat_completions path; sk-kimi-* keys are
redirected to api.kimi.com/coding by core).

User override of the bundled kimi-coding plugin: the bundled ``fetch_models``
inherits ``ProviderProfile.fetch_models``, which sends the profile's
``Accept-Encoding: gzip`` header over urllib but never decompresses the body —
``resp.read().decode()`` then fails on the gzip magic bytes and the picker
falls back to the stale curated list (missing kimi-k3.1-pro). This override
performs the listing fetch itself, gzip-aware.
"""

import gzip
import json
import urllib.request
from typing import Any
from urllib.parse import urlparse

from agent.reasoning_effort import KIMI_K3_EFFORTS, KIMI_K3_OVERRIDES, thinking_toggle_extras
from hermes_cli.version_info import hermes_user_agent
from providers import register_provider
from providers.base import OMIT_TEMPERATURE, LazyHeaders, ProviderProfile

_HEADERS = {
    "HTTP-Referer": "https://hermes-agent.nousresearch.com",
    "X-Title": "Hermes Agent",
    "User-Agent": lambda: hermes_user_agent("HermesAgent"),
    # Exclude brotli: httpx's brotlicffi backend has a streaming decode bug on
    # Moonshot's content-encoding: br SSE responses (#28043, #48428, #59556).
    # gzip sidesteps it while still compressing the transfer.
    "Accept-Encoding": "gzip",
}


def _is_confirmed_kimi_coding_url(base_url: str) -> bool:
    """True only for Kimi Code's canonical HTTPS API surfaces."""
    try:
        p = urlparse(base_url)
        port = p.port
    except ValueError:
        return False
    return (
        p.scheme.lower() == "https" and (p.hostname or "").lower() == "api.kimi.com" and port in (None, 443)
        and p.username is None and p.password is None
        and p.path.rstrip("/") in {"/coding", "/coding/v1"} and not p.query and not p.fragment
    )


class KimiProfile(ProviderProfile):
    """Kimi/Moonshot — temperature omitted, thinking xor reasoning_effort."""

    def fetch_models(
        self, *, api_key: str | None = None, base_url: str | None = None, timeout: float = 8.0
    ) -> list[str] | None:
        """Gzip-safe /models listing. Same endpoint rules as the bundled plugin:
        Kimi Code's OpenAI-compatible surface is used for discovery, and the bare
        ``k3`` slug (only served there) is filtered off other endpoints."""
        effective_base = (base_url or self.base_url or "").rstrip("/")
        confirmed_coding_endpoint = _is_confirmed_kimi_coding_url(effective_base)
        if confirmed_coding_endpoint and urlparse(effective_base).path.rstrip("/") == "/coding":
            effective_base += "/v1"
        if not effective_base:
            return None

        from hermes_cli.urllib_security import open_credentialed_url

        req = urllib.request.Request(effective_base + "/models")
        if api_key:
            req.add_header("Authorization", f"Bearer {api_key}")
        req.add_header("Accept", "application/json")
        for header, value in self.default_headers.items():
            req.add_header(header, value)

        try:
            with open_credentialed_url(req, timeout=timeout) as resp:
                raw = resp.read()
                if "gzip" in (resp.headers.get("Content-Encoding") or "").lower():
                    raw = gzip.decompress(raw)
                data = json.loads(raw.decode())
            items = data if isinstance(data, list) else data.get("data", [])
            from hermes_cli.chat_catalog import chat_catalog_ids

            models = chat_catalog_ids(items)
        except Exception:
            return None
        if models is None or confirmed_coding_endpoint:
            return models
        return [model for model in models if model.strip().lower() != "k3"]

    def build_api_kwargs_extras(
        self, *, reasoning_config: dict | None = None, model: str | None = None, **context
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Moonshot treats extra_body.thinking and reasoning_effort as mutually
        exclusive (400 on both): send effort when requested, else the toggle. K2.x keeps
        replayed prior-turn reasoning_content only with ``thinking.keep: "all"`` (default null
        ignores it; platform.kimi.ai/docs/guide/use-thinking-models); K3 always keeps it."""
        extra_body, top_level = thinking_toggle_extras(reasoning_config, KIMI_K3_EFFORTS, KIMI_K3_OVERRIDES)
        thinking = extra_body.get("thinking")
        if isinstance(thinking, dict) and thinking.get("type") == "enabled" and "k2" in (model or "").lower():
            extra_body["thinking"] = {**thinking, "keep": "all"}
        return extra_body, top_level


def _kimi(name: str, aliases: tuple, env_vars: tuple, base_url: str) -> KimiProfile:
    return KimiProfile(
        name=name, aliases=aliases, env_vars=env_vars, base_url=base_url,
        fixed_temperature=OMIT_TEMPERATURE, default_max_tokens=32000,
        default_headers=LazyHeaders(_HEADERS), default_aux_model="kimi-k2-turbo-preview",
    )


kimi = _kimi("kimi-coding", ("kimi", "moonshot", "kimi-for-coding"), ("KIMI_API_KEY", "KIMI_CODING_API_KEY"),
             "https://api.moonshot.ai/v1")
kimi_cn = _kimi("kimi-coding-cn", ("kimi-cn", "moonshot-cn"), ("KIMI_CN_API_KEY",), "https://api.moonshot.cn/v1")

register_provider(kimi)
register_provider(kimi_cn)
