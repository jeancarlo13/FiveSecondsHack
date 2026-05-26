import json
import os
import re
from pathlib import Path

from openai import OpenAI

from .config import SOURCE_CONTEXT_LINES
from .state import log_error

_PROMPT_TEMPLATE = (Path(__file__).parent / "prompts" / "refactor.md").read_text(encoding="utf-8")


def ask_llm_for_refactor(rule_id, sonar_message, source_line, file_path, line_number):
    """
    Calls the OpenAI/LLM API to dynamically process the real code smell
    and generate a precise explanation and refactoring suggestion.
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
            temperature=0.2
        )
        result_text = response.choices[0].message.content.strip()
        if result_text.startswith("```"):
            result_text = re.sub(r'^```(?:json)?\s*\n?', '', result_text)
            result_text = re.sub(r'\n?\s*```\s*$', '', result_text).strip()
        return json.JSONDecoder(strict=False).decode(result_text)
    except Exception as e:
        log_error(f"LLM Inference failed: {e} | raw_response={result_text[:300] if result_text else 'N/A'}")
        return {
            "title": "\U0001f6a8 Alerta de Calidad de C\u00f3digo",
            "explanation": f"SonarCloud detect\u00f3 una anomal\u00eda en el c\u00f3digo (<strong>{rule_id}</strong>): {sonar_message}.",
            "suggested_code": source_line
        }
