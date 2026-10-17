"""Bound HTTP requests before multipart parsing, including chunked transfers."""
from starlette.responses import JSONResponse

MAX_REQUEST_BYTES = 10 * 1024 * 1024 + 64 * 1024

class RequestTooLarge(Exception):
    pass

class BodyLimitMiddleware:
    def __init__(self,app): self.app=app

    async def __call__(self,scope,receive,send):
        if scope['type']!='http': return await self.app(scope,receive,send)
        headers=dict(scope['headers'])
        length=headers.get(b'content-length')
        try: too_large=length is not None and int(length)>MAX_REQUEST_BYTES
        except ValueError: too_large=True
        if too_large: return await JSONResponse({'detail':'Request exceeds upload limit'},status_code=413)(scope,receive,send)
        consumed=0
        async def bounded_receive():
            nonlocal consumed
            message=await receive()
            consumed+=len(message.get('body',b''))
            if consumed>MAX_REQUEST_BYTES: raise RequestTooLarge()
            return message
        try: await self.app(scope,bounded_receive,send)
        except RequestTooLarge:
            await JSONResponse({'detail':'Request exceeds upload limit'},status_code=413)(scope,receive,send)
