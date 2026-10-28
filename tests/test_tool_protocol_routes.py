"""Exercise shared DSML variants through all three public HTTP protocols."""
import json

import pytest
from fastapi.testclient import TestClient

import server
from tests.test_responses_api import backend
from tests.test_dsml_variants import report_call


@pytest.fixture
def upstream(backend,monkeypatch):
    monkeypatch.setattr(server,'_acquire',lambda **kwargs:server._acquire_response(None))
    adapter=server._acquire_response(None).adapter
    original=adapter.chat_stream
    def stream(*args,**kwargs):
        for token in original(*args,**kwargs):
            if isinstance(token,str): yield from token
            else: yield token
    monkeypatch.setattr(adapter,'chat_stream',stream)
    return backend


def post(http,protocol,stream):
    schema={'type':'object','properties':{'cmd':{'type':'string'}},
        'required':['cmd'],'additionalProperties':False}
    function={'name':'exec_command','parameters':schema,'strict':True}
    payload={'model':'deepseek-chat','stream':stream}
    if protocol=='responses':
        payload.update(input='run command',tools=[{'type':'function',**function}])
    else:
        payload['messages']=[{'role':'user','content':'run command'}]
        if protocol=='messages':
            payload.update(max_tokens=1024,tools=[{'name':'exec_command','input_schema':schema}])
        else: payload['tools']=[{'type':'function','function':function}]
    path={'responses':'responses','messages':'messages','chat':'chat/completions'}[protocol]
    result=http.post('/v1/'+path,json=payload)
    assert result.status_code==200, result.text
    return result


def events(response):
    rows,event_type=[],None
    for line in response.text.splitlines():
        if not line: event_type=None
        elif line.startswith('event: '): event_type=line[7:]
        elif line.startswith('data: ') and line[6:]!='[DONE]':
            row=json.loads(line[6:])
            if event_type: row.setdefault('type',event_type)
            rows.append(row)
    return rows


@pytest.mark.parametrize('protocol',['responses','messages','chat'])
@pytest.mark.parametrize('stream',[False,True])
@pytest.mark.parametrize('bars',['｜','｜｜','||','|｜|'])
def test_variant_is_a_tool_call_without_raw_markup_in_all_routes(upstream,protocol,stream,bars):
    upstream.state.text=report_call(bars=bars)
    with TestClient(server.app) as http: response=post(http,protocol,stream)
    if protocol=='responses':
        rows=events(response) if stream else []
        result=rows[-1]['response'] if stream else response.json()
        if stream: assert rows[-1]['type']=='response.completed'
        calls=[item for item in result['output'] if item['type']=='function_call']
        assert len(calls)==1 and calls[0]['name']=='exec_command'
        assert json.loads(calls[0]['arguments'])=={'cmd':'echo hello'}
        visible=''.join(part['text'] for item in result['output'] if item['type']=='message' for part in item['content'])
    elif protocol=='messages':
        if stream:
            rows=events(response)
            calls=[row['content_block'] for row in rows if row['type']=='content_block_start' and row['content_block']['type']=='tool_use']
            args=''.join(row['delta']['partial_json'] for row in rows if row['type']=='content_block_delta' and row['delta']['type']=='input_json_delta')
            assert json.loads(args)=={'cmd':'echo hello'}
            visible=''.join(row['delta']['text'] for row in rows if row['type']=='content_block_delta' and row['delta']['type']=='text_delta')
            assert any(row['type']=='message_delta' and row['delta']['stop_reason']=='tool_use' for row in rows)
        else:
            result=response.json()
            calls=[item for item in result['content'] if item['type']=='tool_use']
            assert calls[0]['input']=={'cmd':'echo hello'}
            visible=''.join(item['text'] for item in result['content'] if item['type']=='text')
            assert result['stop_reason']=='tool_use'
        assert len(calls)==1 and calls[0]['name']=='exec_command'
    else:
        if stream:
            choices=[row['choices'][0] for row in events(response)]
            deltas=[choice['delta'] for choice in choices]
            calls=[call for delta in deltas for call in delta.get('tool_calls',[])]
            names=[call['function']['name'] for call in calls if call['function'].get('name')]
            assert names==['exec_command']
            args=''.join(call['function'].get('arguments','') for call in calls)
            assert json.loads(args)=={'cmd':'echo hello'}
            visible=''.join(delta.get('content') or '' for delta in deltas)
            assert choices[-1]['finish_reason']=='tool_calls'
        else:
            choice=response.json()['choices'][0]
            calls=choice['message']['tool_calls']
            assert len(calls)==1 and calls[0]['function']['name']=='exec_command'
            assert json.loads(calls[0]['function']['arguments'])=={'cmd':'echo hello'}
            visible=choice['message']['content'] or ''
            assert choice['finish_reason']=='tool_calls'
    assert visible==''
    assert upstream.releases


@pytest.mark.parametrize('content,expected',[
    (report_call(),''),
    ('before '+report_call()+' after','before  after'),
    ('ordinary answer [citation:1]','ordinary answer [citation:1]'),
],ids=['pure_tool','text_and_tool','ordinary_text'])
def test_anthropic_nonstream_preserves_only_intended_visible_text(upstream,content,expected):
    upstream.state.text=content
    with TestClient(server.app) as http: result=post(http,'messages',False).json()
    assert ''.join(item['text'] for item in result['content'] if item['type']=='text')==expected
    assert result['content'][0]=={'type':'thinking','thinking':'thinking'}
