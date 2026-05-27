"""LLM integration for AI-assisted code-smell explanation and refactoring.

Loads the prompt template from ``src/prompts/refactor.md`` once at module
import and reuses it for every call.  Communicates with any OpenAI-compatible
endpoint (GitHub Models, Azure OpenAI, etc.) via the ``openai`` SDK.
"""

import json
import os
import re
from pathlib import Path

from openai import OpenAI

from .config import SOURCE_CONTEXT_LINES
from .state import log_error

# Prompt template loaded once at import time to avoid repeated disk reads.
_PROMPT_TEMPLATE = (Path(__file__).parent / "prompts" / "refactor.md").read_text(encoding="utf-8")


def ask_llm_for_refactor(rule_id, sonar_message, source_line, file_path, line_number):
    """Query the LLM for a structured explanation and refactoring suggestion.

    Formats the prompt template with the supplied issue metadata and source
    code, then calls the configured OpenAI-compatible endpoint.  The model
    is expected to return a JSON object; markdown code fences are stripped
    before parsing.  If inference fails for any reason, a safe fallback dict
    is returned so the notification pipeline can still continue.

    Args:
        rule_id:      SonarCloud rule identifier, e.g. ``"python:S1481"``.
        sonar_message: Human-readable description of the issue as reported by
                       SonarCloud.
        source_line:   Raw source code snippet (multiline string) surrounding
                       the flagged line.
        file_path:     Component path of the affected file.
        line_number:   1-based line number of the issue within the file.

    Returns:
        dict with keys:
            ``title`` (str), ``explanation`` (str),
            ``suggested_code`` (str | list), and
            ``sonar_message_es`` (str, optional).
        On failure, returns a minimal fallback dict with the original
        source as ``suggested_code``.
    """
    client = OpenAI()

    prompt = _PROMPT_TEMPLATE.format(
        file_path=file_path,
        rule_id=rule_id,
        sonar_message=sonar_message,
        line_number=line_number,
        context_lines=2 * SOURCE_CONTEXT_LINES + 1,
        start_line=max(1, line_number - SOURCE_CONTEXT_LINES),
        source_line=source_line,
    )

    result_text = ""
    try:
        response = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        result_text = response.choices[0].message.content.strip()
        # Strip optional ```json ... ``` fences before JSON parsing.
        if result_text.startswith("```"):
            result_text = re.sub(r"^```(?:json)?\s*\n?", "", result_text)
            result_text = re.sub(r"\n?\s*```\s*$", "", result_text).strip()
        return json.JSONDecoder(strict=False).decode(result_text)
    except Exception as e:
        log_error(f"LLM Inference failed: {e} | raw_response={result_text[:300] if result_text else 'N/A'}")
        return {
            "title": "\U0001f6a8 Alerta de Calidad de C\u00f3digo",
            "explanation": f"SonarCloud detect\u00f3 una anomal\u00eda en el c\u00f3digo (<strong>{rule_id}</strong>): {sonar_message}.",
            "suggested_code": source_line,
        }
