from __future__ import annotations

import dataclasses
from typing import Any


@dataclasses.dataclass
class Prompt:
    name: str
    description: str
    arguments: list[dict[str, Any]]


@dataclasses.dataclass
class Resource:
    uri: str
    name: str
    description: str
    mime_type: str = "text/plain"
    _text: str = ""


_code_review_prompt = Prompt(
    name="code_review",
    description="Review source code for potential issues and suggest improvements",
    arguments=[
        {
            "name": "code",
            "description": "Source code to review",
            "required": True,
        },
        {
            "name": "language",
            "description": "Programming language of the code",
            "required": False,
        },
    ],
)

_sample_resource = Resource(
    uri="docs://README",
    name="Project README",
    description="A sample read-only resource",
    _text="# ft_mcp\n\nModel Context Protocol server implementation.",
)

_prompts: dict[str, Prompt] = {}
_resources: dict[str, Resource] = {}


def init() -> None:
    _prompts[_code_review_prompt.name] = _code_review_prompt
    _resources[_sample_resource.uri] = _sample_resource


def list_prompts() -> list[dict[str, Any]]:
    return [
        {
            "name": p.name,
            "description": p.description,
            "arguments": p.arguments,
        }
        for p in _prompts.values()
    ]


async def get_prompt(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any] | None:
    prompt = _prompts.get(name)
    if prompt is None:
        return None

    args = arguments or {}
    code = args.get("code", "")
    language = args.get("language", "unknown")

    messages = [
        {
            "role": "user",
            "content": {
                "type": "text",
                "text": f"Review this {language} code:\n\n```{language}\n{code}\n```",
            },
        }
    ]

    return {
        "description": prompt.description,
        "messages": messages,
    }


def list_resources() -> list[dict[str, Any]]:
    return [
        {
            "uri": r.uri,
            "name": r.name,
            "description": r.description,
            "mimeType": r.mime_type,
        }
        for r in _resources.values()
    ]


async def read_resource(uri: str) -> dict[str, Any] | None:
    resource = _resources.get(uri)
    if resource is None:
        return None
    return {
        "contents": [
            {
                "uri": resource.uri,
                "mimeType": resource.mime_type,
                "text": resource._text,
            }
        ]
    }
