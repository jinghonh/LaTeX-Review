"""Small local control protocol. No publication routes exist here."""
PROTOCOL_VERSION = 1
MAX_BODY = 1024 * 1024
HEADER_TOKEN = 'X-LaTeX-Review-Token'


class LocalError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def valid_id(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)
