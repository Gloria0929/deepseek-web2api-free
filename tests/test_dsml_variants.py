"""Regression fixtures transcribed from the Codex CLI deployment report."""
import json

import pytest
from fastapi.testclient import TestClient

import server
from tool_dsml import parse_dsml_tool_calls
from tool_sieve import StreamSieve
from tests.test_responses_api import backend


def report_call(wrapper='calls', bars='｜', value='echo hello', attributes='string="true"'):
    def tag(name): return f'<{bars}DSML{bars}{name}>'
    def close(name): return f'</{bars}DSML{bars}{name}>'
    return (tag(wrapper)+tag('invoke name="exec_command"')+
        tag(f'parameter name="cmd" {attributes}')+value+close('parameter')+close('invoke')+close(wrapper))


@pytest.mark.parametrize('wrapper',['calls','tool_calls','tool_call'])
@pytest.mark.parametrize('bars',['｜','|'])
def test_report_variant_preserves_command_and_removes_markup(wrapper,bars):
    text=report_call(wrapper,bars)
    calls,cleaned=parse_dsml_tool_calls(text)
    assert len(calls)==1 and cleaned==''
    assert calls[0]['function']['name']=='exec_command'
    assert json.loads(calls[0]['function']['arguments'])=={'cmd':'echo hello'}


@pytest.mark.parametrize('value',['true','123','null','{"x":1}','  echo hello  ',''])
def test_explicit_string_attribute_preserves_string_type(value):
    calls,_=parse_dsml_tool_calls(report_call(value=value))
    assert json.loads(calls[0]['function']['arguments'])=={'cmd':value}


def test_command_cdata_is_not_unicode_normalized_or_interpreted_as_tags():
    value='echo "｜"; echo "<｜DSML｜calls>literal</｜DSML｜calls>"'
    calls,_=parse_dsml_tool_calls(report_call(value=f'<![CDATA[{value}]]>'))
    assert json.loads(calls[0]['function']['arguments'])=={'cmd':value}
    visible,streamed=collect(list(report_call(value=f'<![CDATA[{value}]]>')))
    assert visible=='' and json.loads(streamed[0]['function']['arguments'])=={'cmd':value}


def collect(chunks):
    sieve=StreamSieve(parse_fn=parse_dsml_tool_calls)
    events=[event for chunk in chunks for event in sieve.feed(chunk)]+sieve.flush()
    return ''.join(event.data for event in events if event.type=='text'), [
        call for event in events if event.type=='tool_calls' for call in event.data]


def test_report_variant_streams_at_every_split_without_markup_leakage():
    text=report_call()
    for split in range(len(text)+1):
        visible,calls=collect([text[:split],text[split:]])
        assert visible=='' and len(calls)==1, split
        assert json.loads(calls[0]['function']['arguments'])=={'cmd':'echo hello'}
    assert collect(list(text))[0]==''


def test_wrapper_waits_for_entire_batch_and_preserves_surrounding_text():
    first=report_call().removesuffix('</｜DSML｜calls>')
    second=report_call(value='echo second').removeprefix('<｜DSML｜calls>')
    sieve=StreamSieve(parse_fn=parse_dsml_tool_calls)
    assert sieve.feed(first)==[]
    events=sieve.feed(second+' after')+sieve.flush()
    calls=[call for event in events if event.type=='tool_calls' for call in event.data]
    assert len(calls)==2
    assert ''.join(event.data for event in events if event.type=='text')==' after'


def test_literal_prose_and_incomplete_variant_are_preserved():
    for text in ['ordinary ｜ text <callsign>value</callsign>', report_call()[:-8]]:
        visible,calls=collect(list(text))
        assert visible==text and calls==[]


def test_case_insensitive_tags_keep_original_unicode_offsets():
    visible,calls=collect(['İ before '+report_call().replace('DSML','dsml').replace('calls','CALLS')+' after'])
    assert visible=='İ before  after' and len(calls)==1


def test_non_string_parameters_keep_native_types_and_attribute_order_is_flexible():
    text='<calls><invoke name="f"><parameter string="false" name="count">12</parameter></invoke></calls>'
    calls,cleaned=parse_dsml_tool_calls(text)
    assert cleaned=='' and json.loads(calls[0]['function']['arguments'])=={'count':12}


def test_attribute_names_remain_case_insensitive():
    calls,_=parse_dsml_tool_calls(report_call(value='123').replace('name="cmd"','NAME="cmd"').replace('string="true"','STRING="true"'))
    assert json.loads(calls[0]['function']['arguments'])=={'cmd':'123'}


@pytest.mark.parametrize('stream',[False,True])
def test_responses_returns_function_call_instead_of_raw_report_tags(backend,stream):
    backend.state.text=report_call()
    tools=[{'type':'function','name':'exec_command','strict':True,'parameters':{'type':'object',
        'properties':{'cmd':{'type':'string'}},'required':['cmd'],'additionalProperties':False}}]
    with TestClient(server.app) as http:
        response=http.post('/v1/responses',json={'input':'run command','tools':tools,'stream':stream})
    assert response.status_code==200
    if stream:
        events=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        assert events[-1]['type']=='response.completed'
        result=events[-1]['response']
        assert not any(event['type']=='response.output_text.delta' for event in events)
    else: result=response.json()
    calls=[item for item in result['output'] if item['type']=='function_call']
    assert len(calls)==1 and calls[0]['name']=='exec_command'
    assert json.loads(calls[0]['arguments'])=={'cmd':'echo hello'}
