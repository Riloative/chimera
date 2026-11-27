"""Thinking (reasoning effort): one provider-neutral level, mapped per provider.

Chimera stores only its own level on a conversation (``default``, ``off``,
``on``, ``low``, ``medium``, ``high``, ``max``), never a provider's value. For
every model it works out a capability descriptor (:class:`Caps`) — what the
model can do and how to ask for it — and, per request, clamps the stored level
to what the model supports and turns it into request fields.

Where the descriptor comes from, best source first:

1. the user's override for that model (Models page),
2. what the server says about the model (OpenRouter's model list, Ollama's
   ``/api/show``, the llama-server chat template, Anthropic's Models API),
3. a table of known services (recognised by host) and model names,
4. otherwise nothing: no control is shown and nothing is sent.

A provider that rejects the fields anyway gets one retry without them, and the
model is treated as "no thinking control" for :data:`REJECTED_TTL` seconds.
See docs/ARCHITECTURE.md ("Thinking") for the full mapping table.
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlsplit

LEVELS = ("default", "off", "on", "low", "medium", "high", "max")
ORDER = ("off", "low", "medium", "high", "max")  # effort order, cheapest first
SHAPES = ("none", "toggle", "levels", "always")
OVERRIDES = ("detect", "none", "toggle", "levels")

# Request fields that carry thinking settings: dropped on the retry.
REASONING_FIELDS = ("reasoning_effort", "reasoning", "thinking", "think", "output_config")
REJECTED_TTL = 600.0
_REJECTED: dict[tuple[str, str], float] = {}

# Token budgets for Anthropic models that only take a fixed thinking budget
# (Claude 4.5 and earlier); max_tokens is raised above the budget.
ANTHROPIC_BUDGETS = {"low": 2048, "medium": 8192, "high": 16384, "max": 32000}

# Hosted services reached through their OpenAI-compatible endpoints.
SERVICE_HOSTS = {
    "api.openai.com": "openai",
    "api.mistral.ai": "mistral",
    "generativelanguage.googleapis.com": "gemini",
    "api.deepseek.com": "deepseek",
    "api.x.ai": "xai",
    "openrouter.ai": "openrouter",
    "api.anthropic.com": "anthropic",
}
SERVICES = ("openai", "mistral", "gemini", "deepseek", "xai", "openrouter", "anthropic", "other")


@dataclass(frozen=True)
class Caps:
    """What a model can do with thinking, and how to ask for it."""

    shape: str = "none"  # none | toggle | levels | always
    levels: tuple[str, ...] = ()  # selectable levels besides "default", cheapest first
    flavor: str = "none"  # how the fields are written (see openai_fields and the adapters)
    values: dict[str, Any] = field(default_factory=dict)  # neutral level -> provider value
    default: str | None = None  # what the model does when nothing is sent, if known
    source: str = "none"  # detected | table | override | rejected | none
    off: str | None = None  # Anthropic: how thinking is turned off (disabled | between_tools)

    def public(self) -> dict[str, Any]:
        return {"shape": self.shape, "levels": list(self.levels), "default": self.default, "source": self.source}

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["levels"] = list(self.levels)
        return out

    @classmethod
    def from_dict(cls, data: Any, source: str | None = None) -> "Caps | None":
        if not isinstance(data, dict) or data.get("shape") not in SHAPES:
            return None
        levels = tuple(lv for lv in ORDER + ("on",) if lv in (data.get("levels") or []))
        values = data.get("values") if isinstance(data.get("values"), dict) else {}
        return cls(shape=data["shape"], levels=levels, flavor=str(data.get("flavor") or "none"), values=values,
                   default=data.get("default") if data.get("default") in LEVELS else None,
                   source=source or str(data.get("source") or "detected"), off=data.get("off"))


NONE = Caps()


def toggle(flavor: str, source: str, default: str | None = "on", values: dict | None = None) -> Caps:
    return Caps("toggle", ("off", "on"), flavor, values or {}, default, source)


def levels(flavor: str, source: str, names: tuple[str, ...], values: dict | None = None, default: str | None = None,
           off: str | None = None) -> Caps:
    chosen = tuple(lv for lv in ORDER if lv in names)
    return Caps("levels", chosen, flavor, values or {lv: lv for lv in chosen}, default, source, off)


def always(flavor: str, source: str) -> Caps:
    return Caps("always", (), flavor, {}, "on", source)


# ------------------------------------------------------------------ levels
def clamp(level: str | None, caps: Caps) -> str:
    """The supported level nearest to ``level`` ("default" = send nothing)."""
    if not level or level == "default" or caps.shape in ("none", "always"):
        return "default"
    if level in caps.levels:
        return level
    if caps.shape == "toggle":
        return "off" if level == "off" else "on"
    if level == "on":
        level = caps.default if caps.default in ORDER and caps.default != "off" else "medium"
        if level in caps.levels:
            return level
    if level not in ORDER:
        return "default"
    i = ORDER.index(level)
    # Nearest; on a tie the higher level (the user asked for more than the lower one).
    return min(caps.levels, key=lambda lv: (abs(ORDER.index(lv) - i), -ORDER.index(lv)))


def cheapest(caps: Caps) -> str:
    """For short utility requests (titles, summaries, search queries)."""
    if caps.shape in ("none", "always") or not caps.levels:
        return "default"
    return "off" if "off" in caps.levels else caps.levels[0]


def resolve(level: str | None, caps: Caps, quick: bool) -> str:
    return cheapest(caps) if quick else clamp(level, caps)


# ---------------------------------------------------------------- detection
def service_of(base_url: str, configured: str | None = None) -> str:
    """Which hosted service an OpenAI-compatible connection talks to."""
    if configured and configured in SERVICES:
        return configured
    host = (urlsplit(base_url).hostname or "").lower()
    for known, name in SERVICE_HOSTS.items():
        if host == known or host.endswith("." + known):
            return name
    return "other"


def _bare(model: str) -> str:
    m = model.lower().strip()
    return m.split("/", 1)[1] if m.startswith("models/") else m


def table_caps(service: str, model: str) -> Caps:
    """Known services and model names (checked against each provider's docs;
    see docs/ARCHITECTURE.md). Unknown models get no control."""
    m = _bare(model)
    if service == "openai":
        if re.match(r"o[134](-|$)", m):
            return levels("openai", "table", ("low", "medium", "high"), default="medium")
        if re.match(r"gpt-5(-|$)", m) and "chat" not in m:
            return levels("openai", "table", ("off", "low", "medium", "high"),
                          {"off": "minimal", "low": "low", "medium": "medium", "high": "high"}, default="medium")
        if re.match(r"gpt-(5\.\d|[6-9])", m) and "chat" not in m:
            return levels("openai", "table", ("off", "low", "medium", "high", "max"),
                          {"off": "none", "low": "low", "medium": "medium", "high": "high", "max": "xhigh"}, default="medium")
        return NONE
    if service == "mistral":
        if m.startswith("magistral"):
            return always("mistral", "table")
        if re.match(r"mistral-(small|medium)", m):
            return toggle("mistral", "table", default="off", values={"off": "none", "on": "high"})
        if m.startswith("mistral-large"):
            return levels("mistral", "table", ("low", "medium", "high"), default="medium")
        return NONE
    if service == "gemini":
        if m.startswith("gemini-2.5-flash"):
            return levels("gemini", "table", ("off", "low", "medium", "high"),
                          {"off": "none", "low": "low", "medium": "medium", "high": "high"}, default="medium")
        if m.startswith("gemini-2.5-pro") or re.match(r"gemini-([3-9]|\d\d)", m):
            return levels("gemini", "table", ("low", "medium", "high"), default="high")
        return NONE
    if service == "deepseek":
        if "reasoner" in m:
            return always("deepseek", "table")
        if m.startswith("deepseek"):
            return levels("deepseek", "table", ("off", "low", "high", "max"), default="high")
        return NONE
    if service == "xai":
        if "non-reasoning" in m:
            return NONE
        ver = re.match(r"grok-(\d+)(?:[.-](\d{1,2})(?!\d))?", m)
        if not ver:
            return NONE
        major, minor = int(ver.group(1)), int(ver.group(2) or 0)
        if major == 3 and "mini" in m:
            return levels("xai", "table", ("low", "high"), default="low")
        if (major == 4 and minor >= 6) or major >= 5:
            return levels("xai", "table", ("low", "medium", "high", "max"), {"low": "low", "medium": "medium", "high": "high", "max": "xhigh"},
                          default="medium")
        if major == 4 and minor == 5:
            return levels("xai", "table", ("low", "medium", "high"), default="medium")
        if major == 4 and minor in (3, 4):
            return levels("xai", "table", ("off", "low", "medium"), {"off": "none", "low": "low", "medium": "medium"}, default="low")
        if major == 4:
            return always("xai", "table")
        return NONE
    return NONE


def anthropic_family(model: str) -> dict[str, Any]:
    """How a Claude model thinks (from Anthropic's model documentation):
    ``mode`` adaptive | budget | none, how to turn it off, its default effort
    and whether it thinks when nothing is sent."""
    m = _bare(model)
    if re.search(r"claude-(fable|mythos)", m) or "opus-5-5" in m:
        return {"mode": "adaptive", "off": None, "default": "medium" if "opus-5-5" in m else "high", "thinks": True}
    if "sonnet-5-5" in m:
        return {"mode": "adaptive", "off": "between_tools", "default": "high", "thinks": True}
    if re.search(r"(opus|sonnet|haiku)-5", m):
        return {"mode": "adaptive", "off": "disabled", "default": "medium" if "haiku" in m else "high", "thinks": True}
    if re.search(r"opus-4-[6-9]|sonnet-4-6", m):
        return {"mode": "adaptive", "off": "disabled", "default": "off", "thinks": False}
    if re.search(r"(opus|sonnet|haiku)-4|3-7-sonnet|sonnet-3-7", m):
        return {"mode": "budget", "off": None, "default": "off", "thinks": False}
    if m.startswith("claude-"):
        return {"mode": "none"}
    return {"mode": "adaptive", "off": None, "default": None, "thinks": True}  # a future Claude model


def anthropic_caps(model: str, capabilities: dict | None = None) -> Caps:
    """From the Models API's ``capabilities`` when present, else the family table."""
    fam = anthropic_family(model)
    source = "table"
    mode = fam["mode"]
    effort_levels: tuple[str, ...] = ("low", "medium", "high", "max")
    if isinstance(capabilities, dict) and isinstance(capabilities.get("thinking"), dict):
        source = "detected"
        types = capabilities["thinking"].get("types") or {}

        def ok(node: Any) -> bool:
            return isinstance(node, dict) and node.get("supported") is True

        if ok(types.get("adaptive")):
            mode = "adaptive"
        elif ok(types.get("enabled")):
            mode = "budget"
        elif capabilities["thinking"].get("supported") is False:
            mode = "none"
        effort = capabilities.get("effort")
        if isinstance(effort, dict) and effort.get("supported") is True:
            found = tuple(lv for lv in ("low", "medium", "high", "max") if ok(effort.get(lv)))
            effort_levels = found or effort_levels
    if mode == "none":
        return NONE
    if mode == "budget":
        return levels("anthropic_budget", source, ("off", "low", "medium", "high", "max"),
                      {k: v for k, v in ANTHROPIC_BUDGETS.items()}, default="off")
    names = effort_levels + (("off",) if fam.get("off") else ())
    return levels("anthropic", source, names, default=fam.get("default"), off=fam.get("off")) if names else NONE


def llama_template_caps(template: str | None) -> Caps:
    """llama-server passes ``chat_template_kwargs`` to the model's chat
    template: what the template reads tells what the model can do."""
    if not isinstance(template, str) or not template:
        return NONE
    if "enable_thinking" in template:
        return toggle("llama_toggle", "detected")
    if "reasoning_effort" in template:
        return levels("llama_effort", "detected", ("low", "medium", "high"), default="medium")
    if "<think>" in template:
        return always("llama", "detected")
    return NONE


def override_caps(choice: str | None, kind: str, service: str) -> Caps | None:
    """The user's override for a model on the Models page."""
    if not choice or choice == "detect":
        return None
    if choice == "none":
        return Caps(source="override")
    if kind == "llamacpp":
        flavor_t, flavor_l = "llama_toggle", "llama_effort"
    elif kind == "ollama":
        flavor_t = flavor_l = "ollama"
    elif kind == "anthropic":
        caps = levels("anthropic", "override", ("off", "low", "medium", "high", "max"), off="disabled")
        return caps if choice == "levels" else toggle("anthropic", "override", default=None)
    elif service in ("openai", "mistral", "gemini", "xai", "deepseek", "openrouter"):
        flavor_t = flavor_l = service
    else:
        flavor_t, flavor_l = "generic_toggle", "generic_effort"
    if choice == "toggle":
        values = {"off": "none", "on": "high"} if flavor_t in ("openai", "mistral", "gemini", "xai", "generic_toggle") else {}
        return toggle(flavor_t, "override", default=None, values=values)
    return levels(flavor_l, "override", ("off", "low", "medium", "high"),
                  {"off": "none", "low": "low", "medium": "medium", "high": "high"}, default=None)


def rejected(provider_id: str, model: str) -> bool:
    until = _REJECTED.get((provider_id, model))
    if until and until > time.monotonic():
        return True
    _REJECTED.pop((provider_id, model), None)
    return False


def mark_rejected(provider_id: str, model: str) -> None:
    _REJECTED[(provider_id, model)] = time.monotonic() + REJECTED_TTL


def caps_for(kind: str, base_url: str, model: str, *, provider_id: str = "", service: str | None = None,
             detected: Any = None, override: str | None = None, capabilities: dict | None = None) -> Caps:
    """The descriptor for one model of one connection (see the module docstring)."""
    svc = service_of(base_url, service) if kind == "openai" else kind
    chosen = override_caps(override, kind, svc)
    if chosen is not None:
        return chosen
    if provider_id and rejected(provider_id, model):
        return Caps(source="rejected")
    found = Caps.from_dict(detected)
    if found is not None:
        return found
    if kind == "llamacpp":
        # What the chat template reads (llama_template_caps), found by check().
        return Caps.from_dict((capabilities or {}).get("reasoning")) or NONE
    if kind == "anthropic":
        return anthropic_caps(model)
    if kind == "openai":
        return table_caps(svc, model)
    return NONE


# ------------------------------------------------------------- the mapping
def openai_fields(caps: Caps, level: str) -> dict[str, Any]:
    """Request fields for an OpenAI-compatible server (llama-server too)."""
    if level == "default":
        return {}
    value = caps.values.get(level, level)
    f = caps.flavor
    if f in ("llama_toggle", "generic_toggle") and caps.shape == "toggle":
        return {"chat_template_kwargs": {"enable_thinking": level != "off"}}
    if f == "llama_effort":
        return {"chat_template_kwargs": {"reasoning_effort": value}}
    if f in ("openai", "mistral", "gemini", "xai", "generic_effort", "generic_toggle"):
        return {"reasoning_effort": value}
    if f == "deepseek":
        if level == "off":
            return {"thinking": {"type": "disabled"}}
        return {"thinking": {"type": "enabled"}, "reasoning_effort": value}
    if f == "openrouter":
        if level == "off":
            return {"reasoning": {"enabled": False}}
        return {"reasoning": {"effort": value}}
    return {}


def ollama_fields(caps: Caps, level: str) -> dict[str, Any]:
    if level == "default" or caps.flavor not in ("ollama",):
        return {}
    if caps.shape == "toggle":
        return {"think": level != "off"}
    if level == "off":
        return {"think": False}
    return {"think": caps.values.get(level, level)}


def openrouter_caps(item: dict) -> Caps | None:
    """OpenRouter's model list says which models reason and at what efforts."""
    params = item.get("supported_parameters") if isinstance(item.get("supported_parameters"), list) else []
    info = item.get("reasoning") if isinstance(item.get("reasoning"), dict) else None
    if "reasoning" not in params and info is None:
        return None
    efforts = [str(e) for e in (info or {}).get("supported_efforts") or [] if isinstance(e, str)]
    mandatory = bool((info or {}).get("mandatory"))
    values: dict[str, str] = {}
    for lv in ("low", "medium", "high"):
        if not efforts or lv in efforts:
            values[lv] = lv
    if "max" in efforts or "xhigh" in efforts:
        values["max"] = "max" if "max" in efforts else "xhigh"
    names = tuple(values) + (() if mandatory else ("off",))
    default = (info or {}).get("default_effort")
    return levels("openrouter", "detected", names, values, default=default if default in ORDER else None)
