class SyntheticWorkFailure(Exception):
    """Raised when a controlled failure is requested."""

    def __init__(self, request_id: str):
        self.request_id = request_id
        super().__init__(f"Synthetic failure requested. Request ID: {request_id}")
