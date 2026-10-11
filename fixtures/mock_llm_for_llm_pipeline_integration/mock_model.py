"""Mock Chat Model for offline scenario verification in the Server repository."""

from __future__ import annotations

import json
from typing import Any, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda


class MockChatModel(BaseChatModel):
    """LangChain mock chat model supporting canned responses, structured output, and call history."""

    responses: list[Any] = []
    call_history: list[Any] = []


    def __init__(self, responses: Optional[list[Any]] = None, **kwargs: Any):
        super().__init__(**kwargs)
        object.__setattr__(self, "responses", list(responses or []))
        object.__setattr__(self, "call_history", [])
        object.__setattr__(self, "_idx", 0)

    def _get_next_response(self) -> Any:
        if not self.responses:
            return "{}"
        idx = getattr(self, "_idx", 0)
        if idx < len(self.responses):
            resp = self.responses[idx]
            object.__setattr__(self, "_idx", idx + 1)
        else:
            resp = self.responses[-1]
        return resp

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.call_history.append(messages)
        resp = self._get_next_response()

        if isinstance(resp, AIMessage):
            ai_msg = resp
        elif isinstance(resp, dict):
            ai_msg = AIMessage(
                content=json.dumps(resp),
                tool_calls=resp.get("tool_calls", []),
            )
        else:
            ai_msg = AIMessage(content=str(resp))

        return ChatResult(generations=[ChatGeneration(message=ai_msg)])

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Any:
        def _parse(input_val: Any) -> Any:
            resp = self._get_next_response()
            if isinstance(resp, str):
                try:
                    data = json.loads(resp)
                except Exception:
                    data = {"status_label": "Mock Plan", "assessment": resp, "actions": []}
            elif isinstance(resp, dict):
                data = resp
            else:
                data = resp

            if hasattr(schema, "model_validate"):
                return schema.model_validate(data)
            return data

        return RunnableLambda(_parse)

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        return self

    @property
    def _llm_type(self) -> str:
        return "mock_chat_model"
