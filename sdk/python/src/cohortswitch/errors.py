class CohortSwitchError(Exception):
    def __init__(
        self, message: str, *, code: str = "sdk_error", status_code: int | None = None
    ) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(message)
