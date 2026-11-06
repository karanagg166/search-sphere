"""Emit only completed responses, with fresh authorization before each SSE frame."""

import json

from src.inference.policy import ProviderError
from src.security.query_access import current_query_access


async def authorized_frames(frames, *, versioned=False):
    for event, payload in frames:
        access=current_query_access()
        try:
            if access is not None:
                await access.validate()
        except ProviderError as exc:
            failure={"code":exc.code,"error":"Response access changed."}
            if versioned:
                yield f"data: {json.dumps({'event':'error',**failure})}\n\n"
                yield "data: [DONE]\n\n"
            else:
                yield f"event: error\ndata: {json.dumps(failure)}\n\n"
            return
        if versioned:
            yield f"data: {json.dumps({'event':event,**payload})}\n\n"
        else:
            yield f"event: {event}\ndata: {json.dumps(payload)}\n\n"
    if versioned:
        yield "data: [DONE]\n\n"
