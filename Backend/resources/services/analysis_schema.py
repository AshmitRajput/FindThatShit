"""Validate and normalize provider analysis before persistence."""
from resources.models import ResourceType
from resources.providers.ai import AIProviderError

MAX_KEYWORDS = 30
MAX_TAGS = 10


def _invalid(message):
    return AIProviderError("LLM_INVALID_OUTPUT", message, True)


def _text(raw, key, max_length, required=False):
    value = raw.get(key, "")
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise _invalid(f"{key} must be text.")
    value = value.strip()[:max_length]
    if required and not value:
        raise _invalid(f"{key} is required.")
    return value


def _text_list(raw, key, max_items, max_length):
    values = raw.get(key, [])
    if not isinstance(values, list):
        raise _invalid(f"{key} must be a list.")
    cleaned, seen = [], set()
    for item in values:
        if not isinstance(item, str):
            continue
        item = " ".join(item.split())[:max_length]
        if item and item.lower() not in seen:
            seen.add(item.lower())
            cleaned.append(item)
    return cleaned[:max_items]


def validate_analysis(raw):
    if not isinstance(raw, dict):
        raise _invalid("LLM output must be a JSON object.")
    resource_type = _text(raw, "resource_type", 30).upper()
    return {
        "title": _text(raw, "title", 255),
        "resource_type": resource_type if resource_type in ResourceType.values else "",
        "resource_description": _text(raw, "resource_description", 10_000, required=True),
        "source_description": _text(raw, "source_description", 10_000),
        "summary": _text(raw, "summary", 10_000),
        "keywords": _text_list(raw, "keywords", MAX_KEYWORDS, 100),
        "tags": _text_list(raw, "tags", MAX_TAGS, 50),
        "language": _text(raw, "language", 10).lower(),
    }
