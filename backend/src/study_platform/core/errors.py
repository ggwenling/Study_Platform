from uuid import uuid4

from fastapi import Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, retryable: bool = False):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(message)

    def payload(self, request_id: str | None = None) -> dict:
        return {"code": self.code, "message": self.message,
                "request_id": request_id or str(uuid4()), "retryable": self.retryable}


async def api_error_handler(request: Request, error: ApiError) -> JSONResponse:
    return JSONResponse(
        error.payload(getattr(request.state, "request_id", None)), status_code=error.status_code
    )
