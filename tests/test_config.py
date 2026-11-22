"""Tests for the configuration system."""

from pathlib import Path

import yaml

from tpet.config import LLMProvider, PipelineProviderConfig, TpetConfig, load_config, save_config


class TestTpetConfig:
    """Tests for TpetConfig model."""

    def test_defaults(self) -> None:
        config = TpetConfig()
        assert config.comment_interval_seconds == 30.0
        assert config.idle_chatter_interval_seconds == 300.0
        assert config.max_comments_per_session == 0
        assert config.log_level == "WARNING"
        assert config.ascii_art_frames == 6
        assert config.sleep_threshold_seconds == 120

    def test_default_providers(self) -> None:
        config = TpetConfig()
        # Profile defaults to Claude
        assert config.profile_provider_config.provider == LLMProvider.CLAUDE
        resolved = config.resolved_profile_provider
        assert resolved.model == "claude-haiku-4-5"
        assert resolved.uses_agent_sdk

        # Commentary defaults to Claude
        assert config.commentary_provider_config.provider == LLMProvider.CLAUDE
        resolved = config.resolved_commentary_provider
        assert resolved.model == "claude-haiku-4-5"

        # Image art defaults to OpenAI
        assert config.image_art_provider_config.provider == LLMProvider.OPENAI
        resolved = config.resolved_image_art_provider
        assert resolved.model == "gpt-image-2.6-sunburst"
        assert resolved.is_openai_compat

    def test_config_dir_default(self) -> None:
        config = TpetConfig()
        assert config.config_dir.name == "tpet"

    def test_pipeline_config_resolve_fills_defaults(self) -> None:
        cfg = PipelineProviderConfig(provider=LLMProvider.OLLAMA)
        resolved = cfg.resolve("text")
        assert resolved.model == "llama3.2"
        assert resolved.base_url == "http://localhost:11434/v1"
        assert resolved.api_key == "ollama"

    def test_pipeline_config_resolve_preserves_explicit(self) -> None:
        cfg = PipelineProviderConfig(
            provider=LLMProvider.OLLAMA,
            model="gemma2:2b",
            base_url="http://custom:11434/v1",
        )
        resolved = cfg.resolve("text")
        assert resolved.model == "gemma2:2b"
        assert resolved.base_url == "http://custom:11434/v1"

    def test_pipeline_config_openrouter(self) -> None:
        cfg = PipelineProviderConfig(provider=LLMProvider.OPENROUTER)
        resolved = cfg.resolve("text")
        assert resolved.base_url == "https://openrouter.ai/api/v1"
        assert resolved.api_key_env == "OPENROUTER_API_KEY"


class TestConfigPersistence:
    """Tests for config load/save."""

    def test_save_and_load(self, tmp_config_dir: Path) -> None:
        config = TpetConfig(config_dir=tmp_config_dir, comment_interval_seconds=60.0)
        config_path = tmp_config_dir / "config.yaml"
        save_config(config, config_path)
        assert config_path.exists()

        loaded = load_config(config_path)
        assert loaded.comment_interval_seconds == 60.0

    def test_load_missing_file_returns_defaults(self, tmp_config_dir: Path) -> None:
        config_path = tmp_config_dir / "nonexistent.yaml"
        loaded = load_config(config_path)
        assert loaded.comment_interval_seconds == 30.0

    def test_load_partial_config(self, tmp_config_dir: Path) -> None:
        config_path = tmp_config_dir / "config.yaml"
        config_path.write_text(
            yaml.dump({"comment_interval_seconds": 120.0}),
            encoding="utf-8",
        )
        loaded = load_config(config_path)
        assert loaded.comment_interval_seconds == 120.0
        # Profile provider defaults preserved
        assert loaded.resolved_profile_provider.model == "claude-haiku-4-5"

    def test_save_and_load_pipeline_config(self, tmp_config_dir: Path) -> None:
        config = TpetConfig(
            config_dir=tmp_config_dir,
            commentary_provider_config=PipelineProviderConfig(
                provider=LLMProvider.OLLAMA,
                model="gemma2:2b",
            ),
        )
        config_path = tmp_config_dir / "config.yaml"
        save_config(config, config_path)

        loaded = load_config(config_path)
        resolved = loaded.resolved_commentary_provider
        assert resolved.provider == LLMProvider.OLLAMA
        assert resolved.model == "gemma2:2b"
        assert resolved.base_url == "http://localhost:11434/v1"
