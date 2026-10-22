class LLMError(Exception):
    """Base class for everything the LLM layer raises."""


class LLMRefusalError(LLMError):
    """The provider's safety system declined to answer."""


class LLMOutputError(LLMError):
    """The model's output was truncated or did not match the expected schema."""


class LLMNotSupportedError(LLMError):
    """This provider does not offer the requested capability."""
