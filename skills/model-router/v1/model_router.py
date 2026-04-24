import asyncio
import time
from typing import Optional, Tuple, AsyncGenerator
import logging as _logging


def _log_fb(e, d=None):
    _logging.getLogger("router").debug("%s %s", e, d or {})


def _get_chains_fb():
    return {}


def _get_creds_fb():
    return None


try:
    from ..registry.manager import get_chains
    from ..utils.logger import log
    from apple.core.providers.credentials_loader import get_credentials_loader
except ImportError:
    get_chains = _get_chains_fb
    log = _log_fb
    get_credentials_loader = _get_creds_fb


class ModelRouter:
    """Routes requests to providers with fallback chains, health tracking, hot-reload, and credential management."""

    def __init__(self):
        self._providers: dict = {}
        self._failures: dict = {}
        self._fail_times: dict = {}
        self._success_counts: dict = {}
        self._credentials_loader = get_credentials_loader()
        try:
            from apple.core.learning.adaptive import AdaptiveLearner

            self._learner = AdaptiveLearner()
        except Exception:
            self._learner = None

    def register(self, name: str, provider):
        self._providers[name] = provider
        log("provider_registered", {"name": name})

    def _get_provider_with_credentials(self, provider_name: str):
        """Get provider instance with credentials injected if needed."""
        provider = self._providers.get(provider_name)
        if provider and hasattr(provider, "credentials_loader"):
            # Inject credentials if provider supports it
            try:
                provider.credentials_loader = self._credentials_loader
            except Exception:
                pass
        return provider

    def _provider_for_model(self, model: str) -> Tuple[Optional[object], str]:
        """Determine provider + clean model name from model string."""
        if model.startswith("cerebras:"):
            return self._get_provider_with_credentials("cerebras"), model[9:]
        if model.startswith("mistral:"):
            return self._get_provider_with_credentials("mistral"), model[8:]
        if model.startswith("groq:"):
            return self._get_provider_with_credentials("groq"), model[5:]
        if model.startswith("anthropic:"):
            return self._get_provider_with_credentials("anthropic"), model[10:]
        if "/" in model or model.endswith(":free"):
            return self._get_provider_with_credentials("openrouter"), model
        if ":" in model:
            return self._get_provider_with_credentials("ollama"), model
        # Default to openrouter for bare model names
        return self._get_provider_with_credentials("openrouter"), model

    def _is_healthy(self, model: str) -> bool:
        failures = self._failures.get(model, 0)
        if failures < 3:
            return True
        cooldown = min(60 * failures, 600)
        return time.time() - self._fail_times.get(model, 0) > cooldown

    def _record_failure(self, model: str, latency: float = None):
        self._failures[model] = self._failures.get(model, 0) + 1
        self._fail_times[model] = time.time()
        if self._learner is not None:
            self._learner.update(model, success=False, latency=latency)

    def _record_success(self, model: str, latency: float = None):
        self._failures.pop(model, None)
        self._success_counts[model] = self._success_counts.get(model, 0) + 1
        if self._learner is not None:
            self._learner.update(model, success=True, latency=latency)

    async def complete(
        self, messages: list, chain: str = "fast", max_tokens: int = 2048, **kwargs
    ) -> str:
        models = get_chains().get(chain, get_chains().get("fast", []))
        if not models:
            raise RuntimeError(f"No models defined for chain '{chain}'")

        last_error = None
        for model in models:
            if not self._is_healthy(model):
                log("model_skipped_unhealthy", {"model": model})
                continue
            provider_obj, model_id = self._provider_for_model(model)
            if provider_obj is None:
                continue
            # One retry with exponential backoff for transient timeouts
            for attempt in range(2):
                t0 = time.time()
                try:
                    result = await provider_obj.complete(
                        messages, model_id, max_tokens=max_tokens, **kwargs
                    )
                    elapsed = time.time() - t0
                    if result:
                        self._record_success(model, latency=elapsed)
                        log(
                            "model_success",
                            {
                                "model": model,
                                "chain": chain,
                                "latency_s": round(elapsed, 2),
                                "attempt": attempt,
                            },
                        )
                        return result
                    break  # empty result → try next model, not retry
                except asyncio.TimeoutError as e:
                    elapsed = time.time() - t0
                    last_error = e
                    log("model_timeout", {"model": model, "attempt": attempt})
                    if attempt == 0:
                        await asyncio.sleep(1.5**attempt)  # 1.5s before retry
                    else:
                        self._record_failure(model, latency=elapsed)
                except Exception as e:
                    elapsed = time.time() - t0
                    self._record_failure(model, latency=elapsed)
                    last_error = e
                    log("model_failed", {"model": model, "error": str(e)[:120]})
                    break  # non-timeout → move to next model immediately

        # Chain exhausted — try global fallback_model from model-intent.yml
        fallback = self._load_fallback_model()
        if fallback:
            try:
                log("model_fallback_attempt", {"fallback": fallback, "chain": chain})
                provider_obj, model_id = self._provider_for_model(fallback)
                if provider_obj is not None:
                    t0 = time.time()
                    result = await provider_obj.complete(
                        messages, model_id, max_tokens=max_tokens, **kwargs
                    )
                    elapsed = time.time() - t0
                    if result:
                        self._record_success(fallback, latency=elapsed)
                        log(
                            "model_fallback_success",
                            {"fallback": fallback, "latency_s": round(elapsed, 2)},
                        )
                        return result
            except Exception as e:
                log(
                    "model_fallback_failed",
                    {"fallback": fallback, "error": str(e)[:120]},
                )
                last_error = e

        raise RuntimeError(
            f"All models in chain '{chain}' and fallback failed. Last: {last_error}"
        )

    async def complete_model(
        self, messages: list, model: str, max_tokens: int = 2048, **kwargs
    ) -> str:
        """Call a specific model directly."""
        provider, model_id = self._provider_for_model(model)
        if provider is None:
            raise RuntimeError(f"No provider available for model: {model}")
        return await provider.complete(
            messages, model_id, max_tokens=max_tokens, **kwargs
        )

    async def stream(
        self, messages: list, chain: str = "fast", max_tokens: int = 2048, **kwargs
    ) -> AsyncGenerator[str, None]:
        models = get_chains().get(chain, [])
        for model in models:
            provider_obj, model_id = self._provider_for_model(model)
            if provider_obj is None:
                continue
            try:
                async for chunk in provider_obj.stream(
                    messages, model_id, max_tokens=max_tokens, **kwargs
                ):
                    yield chunk
                self._record_success(model)
                return
            except Exception as e:
                self._record_failure(model)
                log("stream_model_failed", {"model": model, "error": str(e)[:80]})

    def _load_fallback_model(self) -> str:
        """Read fallback_model from protocols/model-intent.yml. Returns '' on any error."""
        try:
            import pathlib
            import yaml  # type: ignore[import]

            intent_path = (
                pathlib.Path(__file__).parents[4] / "protocols" / "model-intent.yml"
            )
            data = yaml.safe_load(intent_path.read_text())
            return data.get("fallback_model", "")
        except Exception:
            return ""

    def health_report(self) -> dict:
        report = {
            "providers": list(self._providers.keys()),
            "failures": {k: v for k, v in self._failures.items() if v > 0},
            "success_counts": self._success_counts,
            "credentials_loaded": self._credentials_loader is not None,
        }
        if self._learner is not None:
            report["learner_stats"] = self._learner.stats
        return report
