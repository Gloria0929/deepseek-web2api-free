"""Responses HTTP adapter. Runtime ownership is injected by the application."""
from contextlib import aclosing
import json
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError
from starlette.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from adapter import RateLimitError
from chat_service import ChatService
from chat_runtime import AccountStreamingResponse, iterate_upstream, prepare_account, thread_step
from image_inputs import ImageError, ImageLimits
from responses_errors import ResponseError
from responses_format import (ResponsesRequest, normalize_input, identifier, validate_calls,
                              output_items, history_output, response_object)
from responses_stream import ResponseStream


def public_error(error):
    if isinstance(error, ResponseError): return error
    if isinstance(error, ImageError): return ResponseError(str(error), error.status)
    if isinstance(error, HTTPException): return ResponseError(str(error.detail), error.status_code)
    if isinstance(error, RateLimitError): return ResponseError('Upstream rate limited the request', 429, 'rate_limit_exceeded')
    if isinstance(error, ValidationError): return ResponseError('Invalid Responses request', 400)
    return ResponseError('Upstream request failed', 502, 'server_error')


def create_responses_router(*, store, acquire, authenticate, scope, controls, model, mark_error, record):
    router = APIRouter()

    async def dispatch(request, operation):
        try:
            headers = authenticate(request)
            return await operation(headers)
        except Exception as error:
            error = public_error(error)
            return JSONResponse(error.payload(), status_code=error.status)

    @router.get('/v1/responses/{response_id}')
    async def retrieve(response_id: str, request: Request):
        async def operation(headers):
            return JSONResponse(store().get(scope(request), response_id)['response'], headers=headers)
        return await dispatch(request, operation)

    @router.delete('/v1/responses/{response_id}')
    async def delete(response_id: str, request: Request):
        async def operation(headers):
            return JSONResponse(store().delete(scope(request), response_id), headers=headers)
        return await dispatch(request, operation)

    @router.post('/v1/responses')
    async def create(request: Request):
        async def operation(headers):
            try: req = ResponsesRequest.model_validate(await request.json())
            except (ValidationError, ValueError): raise ResponseError('Invalid or unsupported Responses request field', 400) from None
            caller, memory = scope(request), store()
            parent = memory.get(caller, req.previous_response_id) if req.previous_response_id else None
            history = memory.history(caller, req.previous_response_id) if parent else []
            delta, attachments = await run_in_threadpool(normalize_input, req, history)
            messages = history + delta
            if len(json.dumps(messages, ensure_ascii=False).encode()) > memory.max_history_bytes:
                raise ResponseError('Response history exceeds the configured byte limit', 413)
            digests = [d for message in messages for d in message.get('_image_digests', [])]
            if len(digests) > ImageLimits.from_env().max_images:
                raise ResponseError('Too many images in reconstructed history; resend a shorter history', 413)
            prompt_messages = ([{'role':'system','content':req.instructions}] if req.instructions else []) + messages
            if req.tools and req.tool_choice != 'none':
                prompt_messages = [{'role':'system','content':
                    'Call at most one function. Function parameter JSON schemas:\n' + json.dumps(req.tools, ensure_ascii=False)}] + prompt_messages
            prompt = ChatService.build_prompt(prompt_messages, req.chat_tools(), req.chat_choice(), tool_result_limit=None)
            options = controls(req.model)
            def prepare(acquired):
                if parent:
                    if acquired.acct.id != parent['account_id'] or acquired.credential_fingerprint != parent['fingerprint']:
                        raise ResponseError('Response account credentials changed; resend complete input and images', 409, 'response_context_unavailable')
                    acquired.seed_image_files(parent['files'])
                return ChatService.prepare(acquired, prompt, attachments, thinking=options['thinking_enabled'],
                    model_type=options['model_type'], replay_digests=digests)
            acquired, turn = await prepare_account(lambda: acquire(parent), prepare, mark_error)
            response_id, created, started = identifier('resp'), time.time(), time.monotonic()
            ready, names = {}, [t['name'] for t in req.tools]
            def save(response):
                if req.store:
                    memory.put(caller, response, req.previous_response_id, delta + history_output(response['output']),
                               acquired.acct.id, acquired.credential_fingerprint, acquired.prepared_image_files)
            if not req.stream:
                try:
                    result = await thread_step(ChatService.result, acquired, turn, options, names, ready)
                    validate_calls(req, result.calls)
                    response = response_object(req, response_id, model(), output_items(result), prompt, created_at=created)
                    save(response)
                    record((time.monotonic() - started) * 1000, True)
                    return JSONResponse(response, headers=headers)
                except Exception:
                    record((time.monotonic() - started) * 1000, False)
                    raise
                finally: acquired.release()
            async def events():
                wire, calls = ResponseStream(req, response_id, model(), prompt, created), []
                try:
                    for frame in wire.start(): yield frame
                    async with aclosing(iterate_upstream(ChatService.events(acquired, turn, options, names, ready))) as source:
                        async for event in source:
                            if event.kind == 'tool':
                                calls.append(event.call)
                                validate_calls(req, calls)
                            for frame in wire.consume(event): yield frame
                    validate_calls(req, calls)
                    for frame in wire.close_active(): yield frame
                    response = wire.response()
                    save(response)
                    record((time.monotonic() - started) * 1000, True)
                    yield wire.emit('response.completed', response=response)
                except Exception as error:
                    record((time.monotonic() - started) * 1000, False)
                    yield wire.fail(str(public_error(error)))
                finally: acquired.release()
            return AccountStreamingResponse(acquired, events(), media_type='text/event-stream',
                headers={'Cache-Control':'no-cache', **headers})
        return await dispatch(request, operation)

    return router
