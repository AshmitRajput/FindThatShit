import json
from unittest import mock

import requests
from django.test import SimpleTestCase, override_settings

from resources.providers import ai


def fake_response(status=200, body=None, bad_json=False):
    response = mock.Mock()
    response.status_code = status
    if bad_json:
        response.json.side_effect = ValueError("bad json")
    else:
        response.json.return_value = body
    return response


def chat_body(content):
    return {"choices": [{"message": {"content": content}}]}


@override_settings(
    LLM_API_URL="https://llm.test/v1/chat", LLM_API_KEY="secret-llm-key", LLM_MODEL="m",
    EMBEDDING_API_URL="https://emb.test/v1/embeddings", EMBEDDING_API_KEY="secret-emb-key",
    EMBEDDING_MODEL="e", EMBEDDING_DIMENSIONS=3, AI_TIMEOUT_SECONDS=5,
)
class AIProviderTests(SimpleTestCase):
    def post(self, **kwargs):
        patcher = mock.patch.object(ai.requests, "post", **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def assert_error(self, call, code, retryable):
        with self.assertRaises(ai.AIProviderError) as ctx:
            call()
        self.assertEqual(ctx.exception.error_code, code)
        self.assertEqual(ctx.exception.retryable, retryable)
        for secret in ("secret-llm-key", "secret-emb-key"):
            self.assertNotIn(secret, str(ctx.exception))

    def test_analyze_parses_json(self):
        post = self.post(return_value=fake_response(body=chat_body(json.dumps({"title": "T"}))))
        self.assertEqual(ai.analyze_resource({"a": 1}), {"title": "T"})
        self.assertEqual(post.call_args.kwargs["headers"], {"Authorization": "Bearer secret-llm-key"})
        self.assertEqual(post.call_args.kwargs["timeout"], (5, 5))

    def test_analyze_accepts_fenced_json(self):
        self.post(return_value=fake_response(body=chat_body('```json\n{"title": "T"}\n```')))
        self.assertEqual(ai.analyze_resource({}), {"title": "T"})

    def test_analyze_sends_non_ascii_content_unescaped(self):
        post = self.post(return_value=fake_response(body=chat_body("{}")))
        ai.analyze_resource({"post_text": "नमस्ते"})
        self.assertIn("नमस्ते", post.call_args.kwargs["json"]["messages"][1]["content"])

    def test_unusable_llm_output_is_retryable(self):
        for body in (chat_body("not json at all"), chat_body(None), {"unexpected": True}):
            self.post(return_value=fake_response(body=body))
            self.assert_error(lambda: ai.analyze_resource({}), "LLM_INVALID_OUTPUT", True)

    def test_timeout_and_connection_errors_are_retryable(self):
        self.post(side_effect=requests.Timeout("slow"))
        self.assert_error(lambda: ai.analyze_resource({}), "PROVIDER_TIMEOUT", True)
        self.post(side_effect=requests.ConnectionError("down"))
        self.assert_error(lambda: ai.analyze_resource({}), "PROVIDER_UNAVAILABLE", True)

    def test_rate_limit_and_server_errors_are_retryable(self):
        for status in (429, 500, 503):
            self.post(return_value=fake_response(status=status))
            self.assert_error(lambda: ai.analyze_resource({}), "PROVIDER_UNAVAILABLE", True)

    def test_auth_and_client_errors_are_not_retryable(self):
        self.post(return_value=fake_response(status=401))
        self.assert_error(lambda: ai.analyze_resource({}), "PROVIDER_AUTH_ERROR", False)
        self.post(return_value=fake_response(status=400))
        self.assert_error(lambda: ai.analyze_resource({}), "PROVIDER_REJECTED_REQUEST", False)

    def test_non_json_http_body_is_retryable(self):
        self.post(return_value=fake_response(bad_json=True))
        self.assert_error(lambda: ai.analyze_resource({}), "PROVIDER_INVALID_RESPONSE", True)

    def test_missing_configuration_is_not_retryable(self):
        with override_settings(LLM_API_KEY=""):
            self.assert_error(lambda: ai.analyze_resource({}), "PROVIDER_NOT_CONFIGURED", False)
        with override_settings(EMBEDDING_API_URL=""):
            self.assert_error(lambda: ai.embed_text("x"), "PROVIDER_NOT_CONFIGURED", False)

    def test_embed_returns_vector(self):
        self.post(return_value=fake_response(body={"data": [{"embedding": [0.1, 0.2, 0.3]}]}))
        self.assertEqual(ai.embed_text("hello"), [0.1, 0.2, 0.3])

    def test_embed_wrong_length_is_not_retryable(self):
        self.post(return_value=fake_response(body={"data": [{"embedding": [0.1, 0.2]}]}))
        self.assert_error(lambda: ai.embed_text("hello"), "EMBEDDING_DIMENSION_MISMATCH", False)

    def test_embed_unusable_response_is_retryable(self):
        self.post(return_value=fake_response(body={"data": []}))
        self.assert_error(lambda: ai.embed_text("hello"), "PROVIDER_INVALID_RESPONSE", True)
