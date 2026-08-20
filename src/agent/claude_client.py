"""Обёртка над Claude API (Anthropic SDK) с веб-поиском.

Изолирует всю специфику LLM-провайдера. SDK импортируется лениво — модуль
и тесты, подставляющие заглушку, не требуют установленного `anthropic`.

Особенности, заложенные по справке Anthropic:
- server-tool веб-поиск выполняется на стороне Anthropic; в ответе —
  готовый текст с цитатами; ошибки веб-поиска НЕ бросают исключение, а
  приходят блоком-ошибкой (HTTP 200) — их логируем, но не падаем.
- adaptive thinking + effort через output_config.
- длинный агентный ход может вернуть stop_reason="pause_turn" — продолжаем,
  пока модель не завершит ход.
- ретраи 429/5xx делает сам SDK (max_retries); поверх — понятные ошибки.
"""

from __future__ import annotations

import logging

from src.config import AgentConfig, require_env

logger = logging.getLogger("claude")

# Верхняя граница числа продолжений при pause_turn — защита от зацикливания.
_MAX_PAUSE_CONTINUATIONS = 6


class AgentError(RuntimeError):
    """Понятная ошибка вызова Claude API для вывода пользователю."""


class ClaudeClient:
    """Тонкий клиент Claude API под задачи обогащения."""

    def __init__(self, cfg: AgentConfig, api_key: str | None = None) -> None:
        self.cfg = cfg
        # Ключ — только из окружения (R1); можно передать явно (для тестов).
        self._api_key = api_key or require_env("ANTHROPIC_API_KEY")
        self._client = None  # ленивая инициализация SDK

    def _sdk(self):
        if self._client is None:
            import anthropic  # ленивый импорт тяжёлой зависимости

            self._anthropic = anthropic
            self._client = anthropic.Anthropic(
                api_key=self._api_key,
                timeout=float(self.cfg.request_timeout_seconds),
                max_retries=self.cfg.max_retries,
            )
        return self._client

    def _tools(self) -> list[dict]:
        tools: list[dict] = []
        if self.cfg.web_search:
            tools.append({
                "type": self.cfg.web_search_tool_type,
                "name": "web_search",
                "max_uses": self.cfg.max_web_searches,
            })
        if getattr(self.cfg, "web_fetch", False):
            tools.append({
                "type": self.cfg.web_fetch_tool_type,
                "name": "web_fetch",
                "max_uses": self.cfg.max_web_fetches,
            })
        return tools

    def _system_param(self, system: str):
        """Готовит system: с кэшированием большого промпта скилла или строкой."""
        if getattr(self.cfg, "cache_system", False):
            # Кэшируем стабильный системный промпт (префикс) — на пачке авто
            # это ~90% экономии на его токенах (prompt caching).
            return [{
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }]
        return system

    def complete(self, system: str, user: str) -> str:
        """Один запрос к модели. Возвращает собранный текст ответа."""
        client = self._sdk()
        anthropic = self._anthropic

        messages = [{"role": "user", "content": user}]
        tools = self._tools()

        collected: list[str] = []
        try:
            for _ in range(_MAX_PAUSE_CONTINUATIONS + 1):
                kwargs = dict(
                    model=self.cfg.model,
                    max_tokens=self.cfg.max_tokens,
                    system=self._system_param(system),
                    messages=messages,
                    thinking={"type": "adaptive"},
                    output_config={"effort": self.cfg.effort},
                )
                if tools:
                    kwargs["tools"] = tools

                response = client.messages.create(**kwargs)

                self._collect_text(response, collected)

                if response.stop_reason == "refusal":
                    details = getattr(response, "stop_details", None)
                    raise AgentError(
                        f"Модель отклонила запрос (refusal): "
                        f"{getattr(details, 'category', None)}"
                    )

                # Длинный ход: продолжаем с накопленной историей.
                if response.stop_reason == "pause_turn":
                    messages.append({"role": "assistant", "content": response.content})
                    continue

                break
        except AgentError:
            raise
        except anthropic.APIStatusError as exc:  # 4xx/5xx после ретраев SDK
            raise AgentError(
                f"Ошибка Claude API (HTTP {exc.status_code}): {exc.message}"
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise AgentError(f"Сетевая ошибка при вызове Claude API: {exc}") from exc

        text = "\n".join(t for t in collected if t).strip()
        if not text:
            raise AgentError("Пустой ответ модели (нет текстовых блоков)")
        return text

    @staticmethod
    def _collect_text(response, collected: list[str]) -> None:
        """Складывает текстовые блоки; ошибки веб-поиска логирует, не падает."""
        for block in response.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                collected.append(block.text)
            elif btype == "web_search_tool_result":
                content = getattr(block, "content", None)
                # У ошибки content — объект с error_code; у успеха — список.
                if isinstance(content, dict) or hasattr(content, "error_code"):
                    code = getattr(content, "error_code", None) or (
                        content.get("error_code") if isinstance(content, dict) else None
                    )
                    logger.warning("веб-поиск вернул ошибку: %s", code)
