"""OpenAI image generation client with native transparent background support."""

from __future__ import annotations

import base64
import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from openai import OpenAI, omit

from tpet.config import DEFAULT_OPENAI_IMAGE_MODEL

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ImageUsage:
    """Token usage from an OpenAI image API call."""

    input_tokens: int = 0
    output_tokens: int = 0
    input_image_tokens: int = 0
    input_text_tokens: int = 0
    output_image_tokens: int = 0
    image_output_rate: float = 30.0

    @property
    def total_tokens(self) -> int:
        """Total tokens consumed."""
        return self.input_tokens + self.output_tokens

    @property
    def estimated_cost_usd(self) -> float:
        """Estimated cost in USD for direct OpenAI image pricing.

        Rates (per 1M tokens):
            - Text input: $5
            - Image input: $8
            - Image output: $30 for GPT Image 2.x, $32 for legacy estimates
        """
        text_input_cost = self.input_text_tokens * 5.0 / 1_000_000
        image_input_cost = self.input_image_tokens * 8.0 / 1_000_000
        image_output_cost = self.output_image_tokens * self.image_output_rate / 1_000_000
        return text_input_cost + image_input_cost + image_output_cost

    def __iadd__(self, other: ImageUsage) -> ImageUsage:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.input_image_tokens += other.input_image_tokens
        self.input_text_tokens += other.input_text_tokens
        self.output_image_tokens += other.output_image_tokens
        return self


@dataclass
class GenerationResult:
    """Result of a full art generation pipeline."""

    usage: ImageUsage = field(default_factory=ImageUsage)
    frame_count: int = 0
    api_calls: int = 0


class OpenAIImageClient:
    """Client for generating images via OpenAI API with transparent backgrounds.

    Uses the OPENAI_API_KEY environment variable for authentication.
    """

    def __init__(
        self,
        model: str = DEFAULT_OPENAI_IMAGE_MODEL,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        """Initialize the OpenAI image client.

        Args:
            model: Model ID to use for image generation.
            api_key: API key for authentication. Falls back to OPENAI_API_KEY env var.
            base_url: Optional base URL for OpenAI-compatible endpoints (e.g. OpenRouter).

        Raises:
            ValueError: If no API key is available.
        """
        resolved_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not resolved_key:
            raise ValueError(
                "API key is required for art generation. "
                "Pass api_key explicitly or set OPENAI_API_KEY environment variable."
            )
        if base_url:
            self._client = OpenAI(api_key=resolved_key, base_url=base_url)
        else:
            self._client = OpenAI(api_key=resolved_key)
        self._model = model

    @property
    def image_output_rate(self) -> float:
        """Estimated USD per million image output tokens."""
        return 30.0 if self._model.startswith("gpt-image-2") else 32.0

    def _extract_usage(self, result: object) -> ImageUsage:
        """Extract token usage from an API response.

        Args:
            result: OpenAI ImagesResponse object.

        Returns:
            ImageUsage with token counts, or zeros if unavailable.
        """
        usage = getattr(result, "usage", None)
        if usage is None:
            return ImageUsage(image_output_rate=self.image_output_rate)

        input_details = getattr(usage, "input_tokens_details", None)
        output_details = getattr(usage, "output_tokens_details", None)
        return ImageUsage(
            input_tokens=getattr(usage, "input_tokens", 0),
            output_tokens=getattr(usage, "output_tokens", 0),
            input_image_tokens=getattr(input_details, "image_tokens", 0) if input_details else 0,
            input_text_tokens=getattr(input_details, "text_tokens", 0) if input_details else 0,
            output_image_tokens=getattr(output_details, "image_tokens", 0) if output_details else 0,
            image_output_rate=self.image_output_rate,
        )

    def generate_sprite(
        self,
        prompt: str,
        output_path: Path,
        size: Literal["1024x1024", "1536x1024", "1024x1536"] = "1024x1024",
        quality: Literal["low", "medium", "high"] = "high",
    ) -> tuple[Path, ImageUsage]:
        """Generate an image sprite sheet via OpenAI with transparent background.

        Args:
            prompt: Text prompt describing the image to generate.
            output_path: Path to save the generated PNG file.
            size: Image size.
            quality: Image quality.

        Returns:
            Tuple of (path to saved PNG, token usage).

        Raises:
            RuntimeError: If the API call fails or returns no images.
        """
        logger.debug("Calling OpenAI API: model=%s, size=%s, quality=%s", self._model, size, quality)

        try:
            result = self._client.images.generate(
                model=self._model,
                prompt=prompt,
                size=size,
                quality=quality,
                background="transparent",
                output_format="png",
                n=1,
            )
        except Exception as exc:
            raise RuntimeError(f"OpenAI API call failed: {exc}") from exc

        if not result.data:
            raise RuntimeError("OpenAI API returned no images")

        b64_data = result.data[0].b64_json
        if not b64_data:
            raise RuntimeError("OpenAI API returned no image data")

        usage = self._extract_usage(result)
        logger.debug("Usage: input=%d, output=%d", usage.input_tokens, usage.output_tokens)

        image_bytes = base64.b64decode(b64_data)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(image_bytes)
        logger.info("Generated sprite saved to %s (%d bytes)", output_path, len(image_bytes))
        return output_path, usage

    def edit_sprite(
        self,
        image_path: Path,
        prompt: str,
        output_path: Path,
        size: Literal["1024x1024", "1536x1024", "1024x1536", "auto"] = "auto",
        quality: Literal["low", "medium", "high"] = "medium",
    ) -> tuple[Path, ImageUsage]:
        """Edit an existing image to create a variant using images.edit.

        Uses explicit high fidelity on legacy models to keep the output close to the
        input as possible, changing only what the prompt describes.

        Args:
            image_path: Path to the source PNG image.
            prompt: Description of the change to make.
            output_path: Path to save the edited PNG file.
            size: Output image size (default "auto" to match input).
            quality: Image quality.

        Returns:
            Tuple of (path to saved PNG, token usage).

        Raises:
            RuntimeError: If the API call fails or returns no images.
        """
        logger.debug(
            "Calling OpenAI edit API: model=%s, size=%s, quality=%s, input=%s",
            self._model,
            size,
            quality,
            image_path,
        )

        try:
            with open(image_path, "rb") as img_file:
                result = self._client.images.edit(
                    model=self._model,
                    image=img_file,
                    prompt=prompt,
                    size=size,
                    quality=quality,
                    background="transparent",
                    output_format="png",
                    n=1,
                    input_fidelity=omit if self._model.startswith("gpt-image-2") else "high",
                )
        except Exception as exc:
            raise RuntimeError(f"OpenAI edit API call failed: {exc}") from exc

        if not result.data:
            raise RuntimeError("OpenAI edit API returned no images")

        b64_data = result.data[0].b64_json
        if not b64_data:
            raise RuntimeError("OpenAI edit API returned no image data")

        usage = self._extract_usage(result)
        logger.debug("Usage: input=%d, output=%d", usage.input_tokens, usage.output_tokens)

        image_bytes = base64.b64decode(b64_data)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(image_bytes)
        logger.info("Edited sprite saved to %s (%d bytes)", output_path, len(image_bytes))
        return output_path, usage
