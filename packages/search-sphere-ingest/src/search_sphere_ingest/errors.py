"""Public errors carry safe categories, never provider bodies or document text."""


class IngestError(Exception):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        self.code, self.retryable = code, retryable
        super().__init__(code)


class CapabilityUnavailable(IngestError):
    pass


class ResourceLimit(IngestError):
    pass
