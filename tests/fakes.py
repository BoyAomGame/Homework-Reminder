"""Shared test doubles for the LLM interfaces."""

from collections.abc import Callable

from app.llm.base import DetectedIntent, LLMError, ParsedAssignment, TextLLM


class FakeTextLLM(TextLLM):
    """A scriptable stand-in for a text LLM.

    ``result`` backs ``parse_assignment`` (a canned ParsedAssignment or an
    Exception to raise). ``router`` backs ``detect_intent``: pass a
    ``DetectedIntent`` (returned for every message), a ``dict`` mapping the
    incoming text to a ``DetectedIntent``, or a callable ``text -> intent``.
    An Exception (class or instance) is raised instead of returned.
    """

    def __init__(
        self,
        result: ParsedAssignment | Exception | None = None,
        router: "DetectedIntent | dict | Callable | Exception | None" = None,
    ):
        self.result = result
        self.router = router
        self.calls: list[str] = []
        self.contexts: list[str] = []
        self.intent_calls: list[str] = []

    async def parse_assignment(self, text: str, *, prompt_context: str) -> ParsedAssignment:
        self.calls.append(text)
        self.contexts.append(prompt_context)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    async def detect_intent(
        self, text: str, *, prompt_context: str, pending: str
    ) -> DetectedIntent:
        self.intent_calls.append(text)
        router = self.router
        if isinstance(router, dict):
            router = router.get(text)
        elif callable(router) and not isinstance(router, DetectedIntent):
            router = router(text)
        if isinstance(router, type) and issubclass(router, Exception):
            raise router("scripted intent failure")
        if isinstance(router, Exception):
            raise router
        if router is None:
            raise LLMError(f"no scripted intent for {text!r}")
        return router


def always_failing_llm() -> FakeTextLLM:
    return FakeTextLLM(LLMError("model returned garbage"))
