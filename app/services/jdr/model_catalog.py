"""Curated catalog of selectable cloud (DeepInfra) models — single source of truth.

The backend owns this catalog: the front fetches it via
``GET /services/jdr/settings/model-catalog`` and never hardcodes model ids or
prices. The PATCH settings endpoint rejects any cloud model id absent from the
matching catalog, so a user cannot select a model the pipeline can't serve
(this is what caused the 70B 401 surfacing deep in a worker).

Tiers order the paid cloud models by cost/quality. The **free** tier is
deliberately *not* a cloud model: it maps to the Ollama (local, self-hosted)
provider, chosen via ``summary_provider=ollama``. This module covers paid
cloud tiers only; the front presents Ollama as the free option separately.

Pricing is indicative (DeepInfra public prices, verified 2026-06) and surfaced
to the front for cost estimation only — it is never used for billing.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel


class ModelTier(StrEnum):
    """Cost/quality tiers for paid cloud models (cheapest → most expensive)."""

    ECONOMY = "economy"
    STANDARD = "standard"
    PREMIUM = "premium"


class TranscriptionPricing(BaseModel):
    """Per-audio-minute billing (transcription / Whisper models)."""

    unit: Literal["per_minute"] = "per_minute"
    price_per_minute: float


class LlmPricing(BaseModel):
    """Per-1M-tokens billing, input and output priced separately (LLMs)."""

    unit: Literal["per_million_tokens"] = "per_million_tokens"
    input_per_1m: float
    output_per_1m: float


class CloudModel(BaseModel):
    """A selectable DeepInfra cloud model with its tier and indicative price."""

    id: str
    label: str
    tier: ModelTier
    pricing: TranscriptionPricing | LlmPricing


# Ids must stay exact and stable: they are persisted in
# ``jdr_model_settings.{transcription,summary}_cloud_model`` and sent verbatim
# to DeepInfra as the model id. The first entry of each tuple is the default.
TRANSCRIPTION_CLOUD_MODELS: tuple[CloudModel, ...] = (
    CloudModel(
        id="openai/whisper-large-v3-turbo",
        label="Whisper Large v3 Turbo",
        tier=ModelTier.ECONOMY,
        pricing=TranscriptionPricing(price_per_minute=0.0002),
    ),
    CloudModel(
        id="openai/whisper-large-v3",
        label="Whisper Large v3",
        tier=ModelTier.STANDARD,
        pricing=TranscriptionPricing(price_per_minute=0.00045),
    ),
)

SUMMARY_CLOUD_MODELS: tuple[CloudModel, ...] = (
    CloudModel(
        id="meta-llama/Meta-Llama-3.1-8B-Instruct",
        label="Llama 3.1 8B Instruct",
        tier=ModelTier.ECONOMY,
        pricing=LlmPricing(input_per_1m=0.02, output_per_1m=0.05),
    ),
    CloudModel(
        id="Qwen/Qwen2.5-72B-Instruct",
        label="Qwen2.5 72B Instruct",
        tier=ModelTier.STANDARD,
        pricing=LlmPricing(input_per_1m=0.36, output_per_1m=0.40),
    ),
    CloudModel(
        id="meta-llama/Meta-Llama-3.1-70B-Instruct",
        label="Llama 3.1 70B Instruct",
        tier=ModelTier.PREMIUM,
        pricing=LlmPricing(input_per_1m=0.40, output_per_1m=0.40),
    ),
)


def transcription_cloud_model_ids() -> frozenset[str]:
    return frozenset(model.id for model in TRANSCRIPTION_CLOUD_MODELS)


def summary_cloud_model_ids() -> frozenset[str]:
    return frozenset(model.id for model in SUMMARY_CLOUD_MODELS)


def is_allowed_transcription_cloud_model(model_id: str) -> bool:
    return model_id in transcription_cloud_model_ids()


def is_allowed_summary_cloud_model(model_id: str) -> bool:
    return model_id in summary_cloud_model_ids()
