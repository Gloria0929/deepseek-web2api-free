"""Explicit supported-subset errors without retaining request data."""
from image_inputs import ImageError


class ResponseError(ImageError):
    def __init__(self, message, status=400, code='invalid_request_error', param=None):
        super().__init__(message, status)
        self.code, self.param = code, param

    def payload(self):
        return {'error': {'message': str(self), 'type': 'invalid_request_error' if self.status < 500 else 'server_error',
                          'code': self.code, 'param': self.param}}
