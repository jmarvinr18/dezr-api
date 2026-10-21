class UnreadableDocument(Exception):
    """The file is stored but its text can't be extracted. The message is shown to the admin,
    so it says what they can do about it."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class EmbeddingUnavailable(Exception):
    """The embedding model could not be reached, or refused the request."""
