"""Normalized input for analysis and embedding."""
from dataclasses import dataclass

from django.conf import settings


@dataclass
class NormalizedContent:
    source_type: str
    creator: str
    title_hint: str
    resource_url: str
    post_text: str
    transcript: str
    document_text: str

    def to_llm_input(self):
        limit = settings.LLM_INPUT_MAX_CHARS
        return {
            "source_type": self.source_type,
            "creator": self.creator,
            "title_hint": self.title_hint,
            "resource_url": self.resource_url,
            "post_text": self.post_text[:limit],
            "transcript": self.transcript[:limit],
            "document_text": self.document_text[:limit],
        }


def build_embedding_text(resource, content):
    tags = [tag.name for tag in resource.tags.all()]
    parts = [
        resource.title, resource.description, resource.summary,
        ", ".join(tags), ", ".join(resource.keywords),
        content.post_text, content.transcript, content.document_text,
    ]
    return "\n".join(part for part in parts if part)[:settings.EMBEDDING_INPUT_MAX_CHARS]
