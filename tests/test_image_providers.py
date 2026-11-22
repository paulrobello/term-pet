"""Offline checks for image generation defaults and provider boundaries."""

from __future__ import annotations

import base64
import json
from io import BytesIO
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import patch

import httpx2
import pytest
import yaml
from openai import DefaultHttpxClient, OpenAI
from PIL import Image
from typer.testing import CliRunner

from tpet.art.client import GeminiImageClient
from tpet.art.generator import generate_art
from tpet.art.openai_client import OpenAIImageClient
from tpet.cli import app
from tpet.config import ArtMode, LLMProvider, PipelineProviderConfig, TpetConfig, save_config
from tpet.models.pet import PetProfile
from tpet.models.rarity import Rarity

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGBA", (2, 2), (0, 0, 0, 0)).save(buffer, "PNG")
    return buffer.getvalue()


def test_openai_default_generates_and_edits_transparent_png(tmp_path: Path, png_bytes: bytes) -> None:
    paths: list[str] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        paths.append(path)
        payload = request.read()
        if path.endswith("/generations"):
            params = json.loads(payload)
            assert params["model"] == "gpt-image-2.6-sunburst"
            assert params["background"] == "transparent"
            assert params["output_format"] == "png"
            assert "input_fidelity" not in params
        else:
            assert request.headers["content-type"].startswith("multipart/form-data")
            assert b"gpt-image-2.6-sunburst" in payload
            assert b'name="background"\r\n\r\ntransparent' in payload
            assert b'name="output_format"\r\n\r\npng' in payload
            assert b'name="input_fidelity"' not in payload
        return httpx2.Response(
            200,
            json={
                "created": 0,
                "data": [{"b64_json": base64.b64encode(png_bytes).decode()}],
                "usage": {
                    "input_tokens": 0,
                    "input_tokens_details": {"image_tokens": 0, "text_tokens": 0},
                    "output_tokens": 1000,
                    "output_tokens_details": {"image_tokens": 1000, "text_tokens": 0},
                    "total_tokens": 1000,
                },
            },
        )

    with (
        OpenAI(
            api_key="fake-openai",
            base_url="https://example.invalid/v1",
            http_client=DefaultHttpxClient(transport=httpx2.MockTransport(respond)),
        ) as sdk,
        patch("tpet.art.openai_client.OpenAI", return_value=sdk),
    ):
        client = OpenAIImageClient(api_key="fake-openai")
        base, usage = client.generate_sprite("base", tmp_path / "base.png")
        final, edit_usage = client.edit_sprite(base, "edit", tmp_path / "edit.png")
    assert paths == ["/v1/images/generations", "/v1/images/edits"]
    assert base.read_bytes() == final.read_bytes() == png_bytes
    assert usage.estimated_cost_usd == edit_usage.estimated_cost_usd == pytest.approx(0.03)


def test_explicit_legacy_openai_model_keeps_fidelity(tmp_path: Path, png_bytes: bytes) -> None:
    base = tmp_path / "base.png"
    base.write_bytes(png_bytes)
    with patch("tpet.art.openai_client.OpenAI") as constructor:
        edit = constructor.return_value.images.edit
        edit.return_value = SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(png_bytes).decode())])
        client = OpenAIImageClient(model="gpt-image-1.5", api_key="fake-openai")
        client.edit_sprite(base, "edit", tmp_path / "edit.png")
        assert edit.call_args.kwargs["input_fidelity"] == "high"


def test_gemini_default_ignores_thought_images(tmp_path: Path, png_bytes: bytes) -> None:
    resolved = PipelineProviderConfig(provider=LLMProvider.GEMINI).resolve("image")
    parts = [
        SimpleNamespace(thought=True, inline_data=SimpleNamespace(data=b"not the final art")),
        SimpleNamespace(inline_data=SimpleNamespace(data=png_bytes)),
    ]
    result = SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=parts))])
    with patch("tpet.art.client.genai.Client") as constructor:
        generate = constructor.return_value.models.generate_content
        generate.return_value = result
        client = GeminiImageClient(api_key="fake-gemini")
        final = client.generate_sprite("sprite", tmp_path / "sprite.png", aspect_ratio="2:3", resolution="2k")
        params = generate.call_args.kwargs
        assert params["model"] == resolved.model == "gemini-nano-banana-2.1"
        assert params["contents"] == ["sprite"]
        assert params["config"].response_modalities == ["TEXT", "IMAGE"]
        assert params["config"].image_config.aspect_ratio == "2:3"
        assert params["config"].image_config.image_size == "2K"
    assert final.read_bytes() == png_bytes


@pytest.mark.parametrize("mode", [ArtMode.SIXEL_ART, ArtMode.MACOS_DESKTOP])
def test_direct_gemini_base_image_rejected_before_credentials(tmp_path: Path, mode: ArtMode) -> None:
    config = TpetConfig(
        config_dir=tmp_path / "config",
        art_mode=mode,
        image_art_provider_config=PipelineProviderConfig(provider=LLMProvider.GEMINI),
    )
    pet = PetProfile(
        name="Mock",
        creature_type="mock",
        rarity=Rarity.COMMON,
        personality="mock",
        backstory="mock",
        ascii_art=[],
        stats={},
        accent_color="red",
    )
    with patch("tpet.config.resolve_api_key") as resolve_api_key:
        with pytest.raises(ValueError, match="--base-image is not supported with Gemini"):
            generate_art(config, pet, base_image_path=tmp_path / "idle.png")
        resolve_api_key.assert_not_called()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("prefix", [[], ["art"]])
def test_cli_rejects_gemini_base_image_before_dotenv_or_profile(tmp_path: Path, prefix: list[str]) -> None:
    idle = tmp_path / "idle.png"
    idle.touch()
    with (
        patch("tpet.cli._load_env") as load_env,
        patch("tpet.profile.generator.generate_pet") as generate_pet,
        patch("tpet.profile.generator.ensure_locomotion_descriptors") as backfill,
    ):
        result = CliRunner().invoke(
            app,
            [
                *prefix,
                "--config-dir",
                str(tmp_path),
                "--art-provider",
                "gemini",
                "--art-mode",
                "macos-desktop",
                "--base-image",
                str(idle),
            ],
        )
        assert result.exit_code == 1
        assert "--base-image is not supported with Gemini" in result.stdout
        load_env.assert_not_called()
        generate_pet.assert_not_called()
        backfill.assert_not_called()
    assert sorted(tmp_path.iterdir()) == [idle]


@pytest.mark.parametrize(
    ("provider", "explicit_model", "expected_model", "expected_url", "expected_key"),
    [
        ("gemini", None, "", "", ""),
        ("gemini", "chosen-gemini-model", "chosen-gemini-model", "", ""),
        ("openai", None, "original", "https://custom.invalid/v1", "CUSTOM_IMAGE_KEY"),
    ],
)
def test_cli_provider_override_binds_fields_to_provider(
    tmp_path: Path,
    provider: str,
    explicit_model: str | None,
    expected_model: str,
    expected_url: str,
    expected_key: str,
) -> None:
    config = TpetConfig(
        config_dir=tmp_path,
        image_art_provider_config=PipelineProviderConfig(
            provider=LLMProvider.OPENAI,
            model="original",
            base_url="https://custom.invalid/v1",
            api_key_env="CUSTOM_IMAGE_KEY",
        ),
    )
    config_path = tmp_path / "config.yaml"
    save_config(config, config_path)
    before = config_path.read_bytes()
    args = ["--dump-config", "--config-dir", str(tmp_path), "--art-provider", provider]
    if explicit_model:
        args.extend(["--art-model", explicit_model])
    with patch("tpet.cli.console.print") as print_config:
        result = CliRunner().invoke(app, args)
    assert result.exit_code == 0
    image_config = yaml.safe_load(print_config.call_args.args[0])["image_art_provider_config"]
    assert image_config == {
        "provider": provider,
        "model": expected_model,
        "base_url": expected_url,
        "api_key_env": expected_key,
    }
    assert config_path.read_bytes() == before
