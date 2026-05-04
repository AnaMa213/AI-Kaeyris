# Contract — `TranscriptionAdapter` (interne)

**Spec** : [`../spec.md`](../spec.md) · **Plan** : [`../plan.md`](../plan.md) · **Research** : [`../research.md`](../research.md) §R1, §R2

> Interface vendor-neutral, dans `app/adapters/transcription.py`. Le code de `app/services/jdr/` ne référence jamais un fournisseur concret (cf. CLAUDE.md §2.4).

---

## Interface (`typing.Protocol`)

```python
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class TranscriptionSegment:
    speaker_label: str            # "speaker_1", "speaker_2", "unknown"
    start_seconds: float
    end_seconds: float
    text: str


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    segments: list[TranscriptionSegment]
    language: str                 # BCP-47, ex: "fr"
    model_used: str               # ex: "openai:whisper-large-v3"
    provider: str                 # "cloud" | "local"


class TranscriptionAdapter(Protocol):
    async def transcribe(
        self,
        *,
        audio_path: str,
        language_hint: str | None = None,
    ) -> TranscriptionResult: ...
```

---

## Erreurs

Reprise des deux familles canoniques du projet (ADR 0004 §4) — l'adapter remappe les erreurs natives en :

```python
class TranscriptionError(Exception): ...
class TransientTranscriptionError(TranscriptionError): ...   # 5xx, timeout, conn error, 429
class PermanentTranscriptionError(TranscriptionError): ...   # 4xx (excl. 429), audio invalide, auth invalide
```

Le job de transcription remappe ensuite vers `TransientJobError` / `PermanentJobError` (cohérent avec `app/jobs/llm.py`).

---

## Implémentation cloud (`OpenAITranscriptionAdapter`)

- Utilise `AsyncOpenAI.audio.transcriptions.create(file=…, model=…, response_format="verbose_json")`.
- Paramétrée par : `provider` ∈ {`openai`, `groq`, `deepinfra`, `together`}, `model`, `api_key`, `base_url` (mêmes defaults que `_DEFAULT_BASE_URLS` de `app/adapters/llm.py`).
- **Note importante** : la diarisation n'est PAS produite par OpenAI Whisper API. Cette implémentation rend tous les segments avec `speaker_label = "unknown"`. Pour obtenir une diarisation, configurer le provider local (cf. R2). Documenté dans le `Risks` du plan.
- Découpage des fichiers > 24 Mo : à implémenter dans la Tasks phase (Whisper API limite 25 Mo).

## Implémentation locale (`OpenAICompatibleLocalAdapter`)

- **Code identique** à `OpenAITranscriptionAdapter` mais paramétrée pour pointer vers un endpoint OpenAI-compatible self-hosted sur l'hôte GPU LAN (`base_url=http://gpu-host:8001/v1`).
- L'hôte GPU expose un wrapper minimal autour de `faster-whisper` + `pyannote.audio`. **Hors scope du repo `ai-kaeyris`** (un repo annexe ou un README de démarrage suffit). À documenter dans `quickstart.md`.
- Le wrapper renvoie `verbose_json` enrichi par segment d'un champ `speaker` (label diarisé). L'adapter le mappe vers `speaker_label`.

## Sélection au runtime

Via `app/core/config.py`, ajout de :

```python
TRANSCRIPTION_PROVIDER: str = "cloud"           # "cloud" | "local"
TRANSCRIPTION_BASE_URL: str = ""                # vide ⇒ default selon provider
TRANSCRIPTION_API_KEY: str = ""                 # peut réutiliser LLM_API_KEY si même fournisseur
TRANSCRIPTION_MODEL: str = "whisper-large-v3"
TRANSCRIPTION_TIMEOUT_SECONDS: float = 1800.0   # 30 min, < FR-018 timeout 60 min
TRANSCRIPTION_LANGUAGE_HINT: str = "fr"
```

Une factory `build_transcription_adapter()` (mêmes principes que `build_llm_adapter()`) instancie l'implémentation. Un mock `MockTranscriptionAdapter` deterministic est fourni pour les tests (renvoie 3 segments fixes en fonction de la longueur du fichier).
