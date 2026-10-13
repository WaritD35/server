"""Where the VLM runs: in-process (transformers) or behind vLLM.

``VLM_PROVIDER`` picks one; both take the same prompt text and the two frames
and return the model's raw text, so :mod:`mlops.assessment.runtime` neither
knows nor cares which ran.

- ``local`` — :class:`LocalTransformersBackend`, ported from PR #15's
  ``vlm_2.py``. For laptops and development. torch and transformers come from the
  opt-in ``vlm-local`` dependency group (``uv sync --group vlm-local``) and are
  imported only when this backend actually generates, so the server image —
  which does not install that group — can import this module.
- ``vllm`` — :class:`VLLMBackend`, an OpenAI-compatible server at
  ``VLM_API_BASE``. For servers and production.
"""

from __future__ import annotations

import base64
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any, Protocol

import openai
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from core.config import get_settings

REPO_ROOT = Path(__file__).resolve().parents[3]


class VLMBackendUnavailableError(RuntimeError):
    """The backend cannot run at all: misconfigured, not installed or unreachable."""


class VLMBackend(Protocol):
    def generate(
        self,
        prompt: str,
        images: list[bytes],
        *,
        model: str,
        temperature: float,
        max_new_tokens: int,
    ) -> str:
        """Ask about ``images`` (JPEG bytes, in prompt order) and return the raw text."""
        ...


class LocalTransformersBackend:
    """InternVL in this process, loaded once per process from a local directory.

    The weights on disk *are* the model, so the ``model`` argument — the model id
    from the prompt config — is ignored here.
    """

    def __init__(self, model_path: str | Path) -> None:
        path = Path(model_path)
        self.model_path = path if path.is_absolute() else REPO_ROOT / path

    def generate(
        self,
        prompt: str,
        images: list[bytes],
        *,
        model: str,
        temperature: float,
        max_new_tokens: int,
    ) -> str:
        try:
            import torch
            from PIL import Image
        except ImportError as error:
            raise VLMBackendUnavailableError(
                "VLM_PROVIDER=local needs the `vlm-local` dependency group: "
                "uv sync --group vlm-local"
            ) from error

        processor, vlm = _load_local_vlm(str(self.model_path))
        # The order here must match the prompt: first Image 1, then Image 2.
        messages = [
            {
                "role": "user",
                "content": [
                    *(
                        {"type": "image", "image": Image.open(BytesIO(data)).convert("RGB")}
                        for data in images
                    ),
                    {"type": "text", "text": prompt},
                ],
            }
        ]
        inputs = processor.apply_chat_template(
            messages,
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
        )

        device = next(vlm.parameters()).device
        inputs = {
            key: value.to(device) if torch.is_tensor(value) else value
            for key, value in inputs.items()
        }

        do_sample = temperature > 0
        generation_args: dict[str, Any] = {
            "max_new_tokens": max_new_tokens,
            "do_sample": do_sample,
            "use_cache": True,
        }
        if do_sample:
            generation_args["temperature"] = temperature

        with torch.inference_mode():
            output_ids = vlm.generate(**inputs, **generation_args)

        generated_ids = output_ids[:, inputs["input_ids"].shape[1] :]
        return str(processor.batch_decode(generated_ids, skip_special_tokens=True)[0])


@lru_cache(maxsize=1)
def _load_local_vlm(model_path: str) -> tuple[Any, Any]:
    """Load the InternVL processor and model once per process."""
    import torch
    from transformers import AutoModelForImageTextToText, AutoProcessor

    # Accept either a local directory or a Hugging Face model id.
    if not Path(model_path).exists() and "/" not in model_path:
        raise VLMBackendUnavailableError(f"VLM model path does not exist: {model_path}")

    processor = AutoProcessor.from_pretrained(model_path, trust_remote_code=True)
    use_cuda = torch.cuda.is_available()
    dtype = torch.bfloat16 if use_cuda and torch.cuda.is_bf16_supported() else (
        torch.float16 if use_cuda else torch.float32
    )
    vlm = AutoModelForImageTextToText.from_pretrained(
        model_path,
        torch_dtype=dtype,
        device_map="auto",
        low_cpu_mem_usage=True,
        trust_remote_code=True,
    )
    return processor, vlm


class VLLMBackend:
    """An OpenAI-compatible VLM server (vLLM), images sent as base64 data URLs."""

    def __init__(self, api_base: str, api_key: str = "") -> None:
        self.api_base = api_base
        self.api_key = api_key

    def generate(
        self,
        prompt: str,
        images: list[bytes],
        *,
        model: str,
        temperature: float,
        max_new_tokens: int,
    ) -> str:
        chat = ChatOpenAI(
            base_url=self.api_base,
            model=model,
            temperature=temperature,
            max_completion_tokens=max_new_tokens,
            # vLLM accepts any key unless started with --api-key; the client
            # still insists on one being present.
            api_key=self.api_key or "unused",  # type: ignore[arg-type]
        )
        message = HumanMessage(
            content=[
                *(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/jpeg;base64,"
                            + base64.b64encode(data).decode("ascii")
                        },
                    }
                    for data in images
                ),
                {"type": "text", "text": prompt},
            ]
        )
        try:
            response = chat.invoke([message])
        except (openai.APIConnectionError, openai.APIStatusError) as error:
            raise VLMBackendUnavailableError(
                f"VLM server at {self.api_base} failed: {error}"
            ) from error
        content = response.content
        if isinstance(content, str):
            return content
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )


def get_vlm_backend() -> VLMBackend:
    """The backend ``VLM_PROVIDER`` names."""
    settings = get_settings()
    provider = (settings.vlm_provider or "").strip().lower()
    if provider == "local":
        return LocalTransformersBackend(settings.vlm_local_model_path)
    if provider == "vllm":
        if not settings.vlm_api_base:
            raise VLMBackendUnavailableError("VLM_PROVIDER=vllm but VLM_API_BASE is not set")
        return VLLMBackend(settings.vlm_api_base, settings.vlm_api_key)
    raise VLMBackendUnavailableError(
        f"unknown VLM_PROVIDER {settings.vlm_provider!r}; expected 'local' or 'vllm'"
    )
