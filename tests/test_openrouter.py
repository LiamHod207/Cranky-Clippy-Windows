"""Offline regression tests. All network responses are mocked."""
import ast
import io
import json
import os
from pathlib import Path
import unittest
import urllib.error
from unittest.mock import patch

import local_secrets
import openrouter_client as client


class Response:
    def __init__(self, data, content_type="application/json"):
        self.data = data
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return self.data


def completion(result, finish_reason="stop"):
    return Response(json.dumps({"choices": [{"message": {"content": json.dumps(result)},
                                             "finish_reason": finish_reason}]}).encode())


class OpenRouterTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-placeholder"})
        self.env.start()
        self.addCleanup(self.env.stop)

    @patch("urllib.request.urlopen")
    def test_structured_request_and_one_key(self, urlopen):
        urlopen.return_value = completion({"mood": "happy", "text": "Good work!"})
        schema = {"type": "object", "properties": {"mood": {"type": "string"}},
                  "required": ["mood"], "additionalProperties": False}
        result = client.json_completion("hello", schema, name="pet", temperature=.8, max_tokens=192)
        self.assertEqual(result["mood"], "happy")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, client.BASE_URL + "/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-placeholder")
        payload = json.loads(request.data)
        self.assertEqual(payload["model"], client.CHAT_MODEL)
        self.assertTrue(payload["provider"]["require_parameters"])
        self.assertEqual(payload["response_format"]["json_schema"]["schema"], schema)

    @patch("urllib.request.urlopen")
    def test_tts_mp3_and_voice(self, urlopen):
        urlopen.return_value = Response(b"ID3mock", "audio/mpeg")
        self.assertEqual(client.spoken_audio("Hi"), b"ID3mock")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, client.BASE_URL + "/audio/speech")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-placeholder")
        self.assertEqual(json.loads(request.data), {
            "model": client.TTS_MODEL, "input": "Hi", "voice": client.TTS_VOICE,
            "response_format": "mp3"})
        self.assertEqual(client.TTS_VOICE, "am_liam")

    @patch("urllib.request.urlopen")
    def test_invalid_tts(self, urlopen):
        for response in [Response(b"", "audio/mpeg"), Response(b'{}'),
                         Response(b"pcm", "audio/pcm;rate=24000")]:
            urlopen.return_value = response
            with self.assertRaises(RuntimeError):
                client.spoken_audio("Hi")

    @patch("urllib.request.urlopen")
    def test_invalid_chat(self, urlopen):
        for response in [Response(b'{}'), Response(b'not-json'),
                         completion([]), completion({"text": "truncated"}, "length"),
                         Response(b'{"choices":[{"message":{"content":null}}]}')]:
            urlopen.return_value = response
            with self.assertRaises(ValueError):
                client.json_completion("Hi", {}, name="pet", temperature=.2, max_tokens=32)

    @patch("urllib.request.urlopen")
    def test_errors_do_not_echo_sensitive_body(self, urlopen):
        for status in (401, 402, 429, 500):
            urlopen.side_effect = urllib.error.HTTPError(client.BASE_URL, status, "error", {},
                                                       io.BytesIO(b"private prompt or key"))
            with self.assertRaisesRegex(RuntimeError, "HTTP %d" % status) as error:
                client.spoken_audio("Hi")
            self.assertNotIn("private", str(error.exception))
        urlopen.side_effect = urllib.error.URLError("offline")
        with self.assertRaisesRegex(RuntimeError, "network"):
            client.spoken_audio("Hi")

    @patch("openrouter_client.get_secret", return_value="")
    @patch("urllib.request.urlopen")
    def test_no_key_or_blank_text(self, urlopen, get_secret):
        self.assertEqual(client.spoken_audio("Hi"), b"")
        self.assertEqual(client.spoken_audio(" "), b"")
        with self.assertRaisesRegex(RuntimeError, "OPENROUTER_API_KEY"):
            client.json_completion("Hi", {}, name="pet", temperature=.2, max_tokens=32)
        urlopen.assert_not_called()


class OverlayTests(unittest.TestCase):
    """Compile only pure overlay functions, so Qt/desktop services are not required."""
    def setUp(self):
        source = Path(__file__).resolve().parents[1] / "clippy_overlay.py"
        tree = ast.parse(source.read_text())
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name in {"_generate_pet_response", "_evaluate_pet_excuse", "_generate_spoken_audio"}]
        self.scope = {"openrouter_client": client, "json": json,
                      "MOOD_ANIMATIONS": {m: {} for m in ("idle", "happy", "sad", "ticked-off", "angry", "very-angry")}}
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), "exec"), self.scope)

    @patch("openrouter_client.has_api_key", return_value=True)
    @patch("openrouter_client.json_completion")
    def test_response_validation_and_deterministic_mood(self, chat, key):
        chat.return_value = {"mood": "idle", "text": "Back to work"}
        result = self.scope["_generate_pet_response"]("escalation", {"stage_mood": "angry", "goal": "write"})
        self.assertEqual(result, ("angry", "Back to work"))
        schema = chat.call_args.args[1]
        self.assertEqual(schema["type"], "object")
        self.assertFalse(schema["additionalProperties"])
        for invalid in [{"mood": "unknown", "text": "hello"}, {"mood": "idle", "text": ""},
                        {"mood": "idle", "text": 4}]:
            chat.return_value = invalid
            with self.assertRaises(ValueError):
                self.scope["_generate_pet_response"]("escalation", {})

    @patch("openrouter_client.has_api_key", return_value=True)
    @patch("openrouter_client.json_completion")
    def test_excuse_boolean_and_text_validation(self, chat, key):
        chat.return_value = {"credible": True, "text": "Keep going"}
        self.assertEqual(self.scope["_evaluate_pet_excuse"]({}, "research"), (True, "Keep going"))
        for invalid in [{"credible": "true", "text": "hello"}, {"credible": False, "text": ""},
                        {"credible": True, "text": 1}]:
            chat.return_value = invalid
            with self.assertRaises(ValueError):
                self.scope["_evaluate_pet_excuse"]({}, "excuse")


class ConfigTests(unittest.TestCase):
    def test_environment_precedes_local_file(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "environment-placeholder"}), \
             patch.object(local_secrets, "_CACHE", {"OPENROUTER_API_KEY": "file-placeholder"}):
            self.assertEqual(local_secrets.get_secret("OPENROUTER_API_KEY"), "environment-placeholder")

    def test_local_config_is_ignored_but_example_is_trackable(self):
        import subprocess
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(["git", "check-ignore", ".env.local"], cwd=root, capture_output=True)
        self.assertEqual(result.returncode, 0)
        result = subprocess.run(["git", "check-ignore", ".env.local.example"], cwd=root, capture_output=True)
        self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
