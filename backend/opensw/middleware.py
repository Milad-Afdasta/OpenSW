from fastapi import HTTPException
from starlette.responses import JSONResponse


class RequestBodyLimit:
    """Bound received bytes before multipart parsing can spool an unlimited file."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in {"GET", "HEAD", "OPTIONS"}:
            return await self.app(scope, receive, send)
        settings = scope["app"].state.settings
        # A 200,000-character field can use twelve ASCII bytes per Unicode
        # character when a JSON client escapes a surrogate pair. Keep every
        # documented valid field representable while bounding malformed input.
        limit = 4 * 1024 * 1024
        if settings.app == "files" and scope["path"] == "/api/files/upload":
            # Account for bounded multipart headers/fields. The file's exact
            # streamed-byte cap is independently enforced in the Files owner.
            limit = settings.max_upload_mb * 1024 * 1024 + 1024 * 1024
        headers = dict(scope.get("headers", []))
        length = headers.get(b"content-length")
        if length is not None:
            try:
                oversized = int(length) > limit
            except ValueError:
                oversized = False
            if oversized:
                response = JSONResponse({"detail": "This request is larger than the configured limit."}, status_code=413)
                return await response(scope, receive, send)
        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise HTTPException(413, "This request is larger than the configured limit.")
            return message

        await self.app(scope, limited_receive, send)
