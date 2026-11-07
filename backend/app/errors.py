class DomainError(Exception):
    def __init__(
        self,
        code: str,
        reason: str,
        status_code: int = 409,
        *,
        missing_fields=None,
        retryable=False,
        next_action="MANUAL_REVIEW",
        details=None,
    ):
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.status_code = status_code
        self.missing_fields = missing_fields or []
        self.retryable = retryable
        self.next_action = next_action
        self.details = details

    def as_dict(self):
        return {
            "code": self.code,
            "reason": self.reason,
            "missing_fields": self.missing_fields,
            "retryable": self.retryable,
            "next_action": self.next_action,
            "details": self.details,
        }
