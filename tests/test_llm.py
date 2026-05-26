"""Tests for src/llm.py."""
import json
from unittest.mock import MagicMock, patch

from src.llm import ask_llm_for_refactor


class TestAskLlmForRefactor:
    _VALID_RESPONSE = json.dumps({
        "title": "🚨 Dead Code",
        "explanation": "Código muerto detectado.",
        "suggested_code": "int x = 0;",
        "sonar_message_es": "Variable no utilizada.",
    })

    def _mock_openai(self, content):
        client_mock = MagicMock()
        choice = MagicMock()
        choice.message.content = content
        client_mock.chat.completions.create.return_value.choices = [choice]
        return client_mock

    def test_returns_parsed_json(self):
        with patch("src.llm.OpenAI", return_value=self._mock_openai(self._VALID_RESPONSE)):
            result = ask_llm_for_refactor("rule", "msg", "code", "file.cs", 5)
        assert result["title"] == "🚨 Dead Code"

    def test_strips_json_code_fence(self):
        fenced = f"```json\n{self._VALID_RESPONSE}\n```"
        with patch("src.llm.OpenAI", return_value=self._mock_openai(fenced)):
            result = ask_llm_for_refactor("rule", "msg", "code", "file.cs", 5)
        assert result["title"] == "🚨 Dead Code"

    def test_strips_plain_code_fence(self):
        fenced = f"```\n{self._VALID_RESPONSE}\n```"
        with patch("src.llm.OpenAI", return_value=self._mock_openai(fenced)):
            result = ask_llm_for_refactor("rule", "msg", "code", "file.cs", 5)
        assert result["title"] == "🚨 Dead Code"

    def test_fallback_on_api_exception(self):
        client_mock = MagicMock()
        client_mock.chat.completions.create.side_effect = Exception("API down")
        with patch("src.llm.OpenAI", return_value=client_mock), \
             patch("src.llm.log_error"):
            result = ask_llm_for_refactor("rule", "msg", "code", "file.cs", 5)
        assert "title" in result
        assert "explanation" in result

    def test_fallback_on_json_parse_error(self):
        with patch("src.llm.OpenAI", return_value=self._mock_openai("not json{")), \
             patch("src.llm.log_error"):
            result = ask_llm_for_refactor("rule", "msg", "code", "file.cs", 5)
        assert "suggested_code" in result

    def test_strict_false_handles_control_chars(self):
        # JSON with a literal tab character inside a string value
        raw = '{"title": "test\ttab", "explanation": "ok", "suggested_code": "x", "sonar_message_es": "y"}'
        with patch("src.llm.OpenAI", return_value=self._mock_openai(raw)):
            result = ask_llm_for_refactor("rule", "msg", "code", "file.cs", 5)
        assert "title" in result
