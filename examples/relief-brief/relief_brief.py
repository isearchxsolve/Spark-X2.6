#!/usr/bin/env python3
"""
ReliefBrief: reviewed disaster-response brief generation with Spark-X2.6-4B.

Run:
  SPARK_BASE_URL=http://127.0.0.1:30000/v1 python relief_brief.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Final
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


MAX_INCIDENT_CHARS: Final[int] = 12_000
MAX_RESPONSE_BYTES: Final[int] = 128_000
MAX_LIST_ITEMS: Final[int] = 8
MODEL_NAME: Final[str] = os.getenv("SPARK_MODEL", "spark2.6")
BASE_URL: Final[str] = os.getenv("SPARK_BASE_URL", "http://127.0.0.1:30000/v1").rstrip("/")
REQUEST_TIMEOUT_SECONDS: Final[float] = float(os.getenv("SPARK_TIMEOUT_SECONDS", "90"))


class BriefError(Exception):
    """An expected, safe-to-return application error."""


@dataclass(frozen=True)
class IncidentInput:
    incident_text: str
    locale: str = "en"

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "IncidentInput":
        if not isinstance(payload, dict):
            raise BriefError("Request body must be a JSON object.")

        text = payload.get("incident_text")
        locale = payload.get("locale", "en")

        if not isinstance(text, str):
            raise BriefError("'incident_text' must be a string.")
        text = text.strip()
        if not text:
            raise BriefError("'incident_text' must not be empty.")
        if len(text) > MAX_INCIDENT_CHARS:
            raise BriefError(
                f"'incident_text' exceeds {MAX_INCIDENT_CHARS} characters."
            )
        if not isinstance(locale, str) or not re.fullmatch(r"[a-z]{2}(-[A-Z]{2})?", locale):
            raise BriefError("'locale' must use a language tag such as 'en' or 'hi-IN'.")

        return cls(incident_text=text, locale=locale)


@dataclass(frozen=True)
class ReliefBrief:
    situation_summary: str
    priority_actions: list[str]
    information_gaps: list[str]
    safety_note: str
    escalation_needed: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _limited_text(value: Any, field: str, maximum: int = 1_500) -> str:
    if not isinstance(value, str):
        raise BriefError(f"Model response field '{field}' must be a string.")
    result = value.strip()
    if not result:
        raise BriefError(f"Model response field '{field}' must not be empty.")
    if len(result) > maximum:
        raise BriefError(f"Model response field '{field}' exceeds {maximum} characters.")
    return result


def _limited_string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list):
        raise BriefError(f"Model response field '{field}' must be an array.")
    if len(value) > MAX_LIST_ITEMS:
        raise BriefError(
            f"Model response field '{field}' exceeds {MAX_LIST_ITEMS} items."
        )

    result: list[str] = []
    for item in value:
        item_text = _limited_text(item, field, maximum=320)
        if item_text not in result:
            result.append(item_text)
    return result


def parse_model_brief(raw_text: str) -> ReliefBrief:
    """Parse only a complete JSON object; reject prose and malformed output."""
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise BriefError("Model returned an empty response.")

    candidate = raw_text.strip()
    fenced = re.fullmatch(r"