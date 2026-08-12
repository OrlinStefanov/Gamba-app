"""Key resolution: custom env var names, .env files, custom endpoints."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gamba.config import (Config, apply_provider_defaults, load_config,
                          load_dotenv_files, save_config, write_env_var)
from gamba.provider import create_provider

CLEAN_ENV = {"OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "", "GAMBA_API_KEY": ""}


def clean_env(**extra):
    """Patch the environment with every key var cleared, plus `extra`."""
    env = dict(CLEAN_ENV)
    env.update(extra)
    return mock.patch.dict(os.environ, env, clear=False)


class TestCustomEnvVarName(unittest.TestCase):
    def test_custom_name_is_used_instead_of_the_default(self):
        config = Config()
        config.model.api_key_env = "MY_SCREEN_KEY"
        with clean_env(MY_SCREEN_KEY="sk-custom"):
            key, source = config.resolve_api_key()
        self.assertEqual(key, "sk-custom")
        self.assertIn("MY_SCREEN_KEY", source)
        self.assertEqual(config.api_key_env_var, "MY_SCREEN_KEY")

    def test_custom_name_does_not_fall_back_to_the_default(self):
        # Falling back would leave the user staring at the wrong variable.
        config = Config()
        config.model.api_key_env = "MY_SCREEN_KEY"
        with clean_env(OPENAI_API_KEY="sk-default"):
            key, source = config.resolve_api_key()
        self.assertEqual(key, "")
        self.assertIn("MY_SCREEN_KEY", source)
        self.assertIn("not set", source)

    def test_config_file_key_wins_over_every_env_var(self):
        config = Config()
        config.model.api_key = "sk-from-config"
        config.model.api_key_env = "MY_SCREEN_KEY"
        with clean_env(MY_SCREEN_KEY="sk-env", OPENAI_API_KEY="sk-default"):
            key, source = config.resolve_api_key()
        self.assertEqual(key, "sk-from-config")
        self.assertIn("config file", source)

    def test_default_name_follows_the_provider(self):
        config = Config()
        self.assertEqual(config.api_key_env_var, "OPENAI_API_KEY")
        apply_provider_defaults(config, "anthropic")
        self.assertEqual(config.api_key_env_var, "ANTHROPIC_API_KEY")
        apply_provider_defaults(config, "custom")
        self.assertEqual(config.api_key_env_var, "GAMBA_API_KEY")

    def test_custom_name_survives_a_config_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            config = Config()
            config.model.api_key_env = "MY_SCREEN_KEY"
            config.model.api_name = "Work gateway"
            save_config(config, path)
            loaded = load_config(path)
        self.assertEqual(loaded.model.api_key_env, "MY_SCREEN_KEY")
        self.assertEqual(loaded.api_label, "Work gateway")


class TestDotEnv(unittest.TestCase):
    def test_reads_keys_from_a_dotenv_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "# a comment\n"
                "\n"
                "MY_SCREEN_KEY=sk-from-file\n"
                "export QUOTED_KEY='sk-quoted'\n"
                'DOUBLE_QUOTED="sk-double"\n'
                "not-a-pair\n",
                encoding="utf-8",
            )
            with clean_env(MY_SCREEN_KEY="", QUOTED_KEY="", DOUBLE_QUOTED=""):
                os.environ.pop("MY_SCREEN_KEY", None)
                os.environ.pop("QUOTED_KEY", None)
                os.environ.pop("DOUBLE_QUOTED", None)
                loaded = load_dotenv_files([path])
                self.assertEqual(loaded, [path])
                self.assertEqual(os.environ["MY_SCREEN_KEY"], "sk-from-file")
                self.assertEqual(os.environ["QUOTED_KEY"], "sk-quoted")
                self.assertEqual(os.environ["DOUBLE_QUOTED"], "sk-double")

    def test_real_env_var_beats_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("OPENAI_API_KEY=sk-from-file\n", encoding="utf-8")
            with clean_env(OPENAI_API_KEY="sk-from-shell"):
                load_dotenv_files([path])
                self.assertEqual(os.environ["OPENAI_API_KEY"], "sk-from-shell")

    def test_missing_file_is_not_an_error(self):
        self.assertEqual(load_dotenv_files([Path("/nonexistent/.env")]), [])

    def test_dotenv_feeds_a_custom_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("MY_SCREEN_KEY=sk-from-file\n", encoding="utf-8")
            config = Config()
            config.model.api_key_env = "MY_SCREEN_KEY"
            with clean_env():
                os.environ.pop("MY_SCREEN_KEY", None)
                load_dotenv_files([path])
                key, source = config.resolve_api_key()
        self.assertEqual(key, "sk-from-file")
        self.assertIn("MY_SCREEN_KEY", source)


class TestWriteEnvVar(unittest.TestCase):
    """`set-key` writes here: it must not clobber neighbours or widen permissions."""

    def test_creates_the_file_with_owner_only_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_env_var(Path(tmp) / "sub" / ".env", "MY_KEY", "sk-secret")
            self.assertEqual(path.read_text(encoding="utf-8"), "MY_KEY=sk-secret\n")
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_replaces_an_existing_value_in_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("OTHER=keep\nMY_KEY=sk-old\nTRAILING=keep\n",
                            encoding="utf-8")
            write_env_var(path, "MY_KEY", "sk-new")
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "OTHER=keep\nMY_KEY=sk-new\nTRAILING=keep\n",
            )

    def test_appends_without_touching_other_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("# comment\nOTHER=keep\n", encoding="utf-8")
            write_env_var(path, "MY_KEY", "sk-new")
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "# comment\nOTHER=keep\nMY_KEY=sk-new\n",
            )

    def test_replaces_an_exported_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("export MY_KEY=sk-old\n", encoding="utf-8")
            write_env_var(path, "MY_KEY", "sk-new")
            self.assertEqual(path.read_text(encoding="utf-8"), "MY_KEY=sk-new\n")

    def test_does_not_match_a_similarly_named_variable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("MY_KEY_BACKUP=sk-other\n", encoding="utf-8")
            write_env_var(path, "MY_KEY", "sk-new")
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "MY_KEY_BACKUP=sk-other\nMY_KEY=sk-new\n",
            )

    def test_written_key_is_then_resolvable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            write_env_var(path, "MY_SCREEN_KEY", "sk-roundtrip")
            config = Config()
            config.model.api_key_env = "MY_SCREEN_KEY"
            with clean_env():
                os.environ.pop("MY_SCREEN_KEY", None)
                load_dotenv_files([path])
                self.assertEqual(config.resolved_api_key(), "sk-roundtrip")


class TestCustomEndpoint(unittest.TestCase):
    def _custom_config(self):
        config = Config()
        apply_provider_defaults(config, "custom")
        config.model.base_url = "https://my-gateway.example.com/v1"
        config.model.model = "my-vision-model"
        config.model.api_name = "Work gateway"
        return config

    def test_custom_provider_uses_the_openai_compatible_client(self):
        from gamba.openai_provider import OpenAIProvider

        config = self._custom_config()
        provider = create_provider(config.model, "sk-test")
        self.assertIsInstance(provider, OpenAIProvider)
        self.assertEqual(str(provider.client.base_url).rstrip("/"),
                         "https://my-gateway.example.com/v1")

    def test_custom_provider_requires_a_base_url(self):
        config = self._custom_config()
        config.model.base_url = ""
        with self.assertRaises(ValueError) as ctx:
            create_provider(config.model, "sk-test")
        self.assertIn("base_url", str(ctx.exception))

    def test_custom_provider_requires_a_model_id(self):
        config = self._custom_config()
        config.model.model = ""
        with self.assertRaises(ValueError) as ctx:
            create_provider(config.model, "sk-test")
        self.assertIn("model", str(ctx.exception))

    def test_api_label_prefers_the_custom_name(self):
        config = self._custom_config()
        self.assertEqual(config.api_label, "Work gateway")
        config.model.api_name = ""
        self.assertEqual(config.api_label, "custom")

    def test_missing_key_message_names_the_custom_variable(self):
        from gamba.openai_provider import OpenAIProvider
        from gamba.provider import MissingAPIKey

        config = self._custom_config()
        config.model.api_key_env = "MY_SCREEN_KEY"
        with self.assertRaises(MissingAPIKey) as ctx:
            OpenAIProvider(config.model, "")
        self.assertIn("MY_SCREEN_KEY", str(ctx.exception))

    def test_base_url_also_applies_to_anthropic(self):
        from gamba.anthropic_provider import AnthropicProvider

        config = Config()
        apply_provider_defaults(config, "anthropic")
        config.model.base_url = "https://claude-proxy.example.com"
        provider = AnthropicProvider(config.model, "sk-test")
        self.assertIn("claude-proxy.example.com", str(provider.client.base_url))


if __name__ == "__main__":
    unittest.main()
