"""The same acceptance contract, using actual sync or async SDK transports."""
import argparse
import asyncio
from contextlib import AsyncExitStack
import inspect
import json
import time
from pathlib import Path
from httpx2 import ReadTimeout

from openai import OpenAI, AsyncOpenAI, DefaultHttpxClient, DefaultAsyncHttpxClient, APIStatusError, APITimeoutError
from tests.test_image_inputs import data_url
from .server_process import ServerHandle

TOOLS = [{'type':'function','name':'weather','parameters':{'type':'object','properties':{'city':{'type':'string'}}}}]
PROBES = json.loads((Path(__file__).parent/'fixtures/images.json').read_text())


async def value(result): return await result if inspect.isawaitable(result) else result


async def events(stream):
    if hasattr(stream,'__aiter__'):
        async for event in stream: yield event
    else:
        for event in stream: yield event


async def close(stream): await value(stream.close())


async def run(client, backend, kind):
    async def create(**kwargs): return await value(client.responses.create(model='deepseek-chat',**kwargs))
    async def check(name, function):
        try:
            await function()
            print(json.dumps({'name':name,'client':kind,'passed':True}),flush=True)
        except Exception:
            print(json.dumps({'name':name,'client':kind,'passed':False}),flush=True)
            raise
    async def idle(stored=None):
        deadline = time.monotonic()+5
        while time.monotonic()<deadline:
            state = backend.state()
            if state['active']==0 and state['busy']==0:
                if stored is not None: assert state['stored']==stored
                return
            await asyncio.sleep(.05)
        raise AssertionError('Account/worker did not drain')
    async def expect_status(status, **kwargs):
        try: await create(**kwargs)
        except APIStatusError as error:
            assert error.status_code == status
            return
        raise AssertionError(f'Expected status {status}')
    async def text():
        result = await create(input='case:text',metadata=None,tool_choice=None)
        assert result.output_text=='answer' and result.status=='completed'
    await check('text',text)
    async def stream():
        response = await create(input='case:text',stream=True)
        seen = []
        try:
            async for event in events(response): seen.append(event)
        finally: await close(response)
        assert seen[-1].type=='response.completed' and seen[-1].response.output_text=='answer'
        assert [e.sequence_number for e in seen]==list(range(len(seen)))
    await check('stream',stream)
    async def aggregate():
        manager = client.responses.stream(model='deepseek-chat',input='case:text')
        async with AsyncExitStack() as stack:
            result = await stack.enter_async_context(manager) if hasattr(manager,'__aenter__') else stack.enter_context(manager)
            async for _ in events(result): pass
            assert (await value(result.get_final_response())).output_text=='answer'
    await check('aggregate',aggregate)
    async def images():
        before=backend.state()['uploads']
        image=await create(input=[{'role':'user','content':[{'type':'input_text','text':'case:image_ack'},
            {'type':'input_image','image_url':data_url()}]}])
        assert image.output_text=='ACK'
        recall=await create(input='case:image_recall',previous_response_id=image.id)
        assert recall.output_text=='red circle' and backend.state()['uploads']==before+1
        compared=await create(input=[{'role':'user','content':[{'type':'input_text','text':'case:image_compare'},
            {'type':'input_image','image_url':data_url(color="blue")}]}],previous_response_id=recall.id)
        assert 'blue squares' in compared.output_text and backend.state()['uploads']==before+2
        await expect_status(502,input=[{'role':'user','content':[{'type':'input_image','image_url':PROBES['failure']}]}],stream=True)
    await check('images',images)
    async def state_and_replay():
        first=await create(input='Remember CEDAR-904',instructions='old rule')
        branch=await create(input='case:code_recall',previous_response_id=first.id,instructions='new rule')
        assert branch.output_text=='CEDAR-904'
        assert (await value(client.responses.retrieve(branch.id))).output_text=='CEDAR-904'
        await value(client.responses.delete(branch.id))
        try: await value(client.responses.retrieve(branch.id))
        except APIStatusError as error: assert error.status_code==404
        else: raise AssertionError('Deleted response retrievable')
        manual=await create(input='case:text',store=False)
        try: await value(client.responses.retrieve(manual.id))
        except APIStatusError as error: assert error.status_code==404
        else: raise AssertionError('store=false response retrievable')
        replay=await create(input=[i.model_dump(exclude_none=True) for i in manual.output]+[{'role':'user','content':'case:text'}],store=False)
        assert replay.output_text=='answer'
    await check('state_and_replay',state_and_replay)
    async def tools():
        response=await create(input='case:batch',tools=TOOLS,parallel_tool_calls=True,tool_choice='required',stream=True)
        calls=[]
        try:
            async for event in events(response):
                if event.type=='response.completed': calls=[i for i in event.response.output if i.type=='function_call']
        finally: await close(response)
        assert len(calls)==2 and len({i.call_id for i in calls})==2
        outputs=[{'type':'function_call_output','call_id':i.call_id,'output':'sunny'} for i in reversed(calls)]
        # Replay the full call Items without stored state as well as the native parent chain.
        manual=await create(input=[i.model_dump(exclude_none=True) for i in calls]+outputs,store=False)
        assert manual.output_text=='tool results received'
        before=backend.state()['uploads']
        outputs[0]['output']=[{'type':'input_text','text':'screenshot'},{'type':'input_image','image_url':PROBES['blue']}]
        result=await create(input=outputs,previous_response_id=event.response.id)
        assert result.output_text=='tool results received'
        assert backend.state()['uploads']==before+1 and backend.state()['requests'][-1]['refs']==1
        await expect_status(400,input=outputs[:1],previous_response_id=event.response.id)
        await expect_status(400,input=[outputs[0],outputs[0]],previous_response_id=event.response.id)
        await expect_status(502,input='case:batch',tools=TOOLS)
    await check('tools',tools)
    async def errors():
        await expect_status(400,input='case:text',max_output_tokens=20)
        try: await value(client.with_options(api_key='invalid').responses.create(model='deepseek-chat',input='x'))
        except APIStatusError as error: assert error.status_code==401
        else: raise AssertionError('Invalid key accepted')
        await expect_status(404,input='x',previous_response_id='resp_missing')
        await expect_status(429,input='case:rate')
        await expect_status(502,input='case:upstream_fail')
        backend.mode('busy')
        try: await expect_status(503,input='case:text')
        finally: backend.mode('idle')
        parent=await create(input='case:text'); backend.mode('credentials')
        await expect_status(409,input='x',previous_response_id=parent.id)
    await check('errors',errors)
    async def failures():
        for prompt,opts in [('case:batch invalid',{'tools':TOOLS,'parallel_tool_calls':True}),
                ('case:midstream_fail',{}),('case:rate',{}),('case:later_invalid',{'tools':TOOLS,'parallel_tool_calls':True})]:
            before=backend.state()['stored']; response=await create(input=prompt,stream=True,**opts); seen=[]
            try:
                async for event in events(response): seen.append(event)
            finally: await close(response)
            assert sum(e.type=='response.failed' for e in seen)==1 and not any(e.type=='response.completed' for e in seen)
            if prompt=='case:batch invalid': assert not any(e.type=='response.output_item.added' and e.item.type=='function_call' for e in seen)
            if prompt=='case:rate': assert seen[-1].response.error.code=='rate_limit_exceeded'
            assert backend.state()['stored']==before
    await check('failures',failures)
    async def abort():
        for prompt,event_type in [('case:slow_first','response.created'),('case:slow_text','response.output_text.delta'),('case:slow_tool','response.function_call_arguments.delta')]:
            before=backend.state()['stored']
            response=await create(input=prompt,stream=True,tools=TOOLS,parallel_tool_calls=True)
            try:
                async for event in events(response):
                    if event.type==event_type: break
                else: raise AssertionError('Abort checkpoint not observed')
            finally: await close(response)
            await idle(before)
            assert (await create(input='case:text')).output_text=='answer'
    await check('abort',abort)
    async def timeout():
        before=backend.state()['stored']; backend.mode('slow_prepare')
        try:
            try: await value(client.with_options(timeout=.15).responses.create(model='deepseek-chat',input='case:text',stream=True))
            except APITimeoutError: pass
            else: raise AssertionError('Expected header timeout')
        finally: backend.mode('normal')
        await idle(before)
        assert (await create(input='case:text')).output_text=='answer'
        for prompt in ('case:slow_text','case:slow_tool'):
            before=backend.state()['stored']
            response=await value(client.with_options(timeout=.15).responses.create(model='deepseek-chat',input=prompt,
                stream=True,tools=TOOLS,parallel_tool_calls=True))
            try:
                try:
                    async for event in events(response):
                        assert event.type!='response.completed'
                except (ReadTimeout,APITimeoutError): pass
                else: raise AssertionError('Expected stream read timeout')
            finally: await close(response)
            await idle(before)
            assert (await create(input='case:text')).output_text=='answer'
    await check('timeout',timeout)


async def main(kind):
    parser=argparse.ArgumentParser(); parser.add_argument('--base-url',required=True); parser.add_argument('--api-key',required=True)
    args=parser.parse_args(); backend=ServerHandle(args.base_url,None,args.api_key)
    async with AsyncExitStack() as stack:
        if kind=='async':
            client=await stack.enter_async_context(AsyncOpenAI(base_url=args.base_url+'/v1',api_key=args.api_key,
                max_retries=0,_strict_response_validation=True,http_client=DefaultAsyncHttpxClient(trust_env=False)))
        else:
            client=stack.enter_context(OpenAI(base_url=args.base_url+'/v1',api_key=args.api_key,
                max_retries=0,_strict_response_validation=True,http_client=DefaultHttpxClient(trust_env=False)))
        await run(client,backend,kind)
