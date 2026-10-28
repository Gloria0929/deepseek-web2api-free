"""Repeated DSML separators must agree across parser and stream capture."""
import json

import pytest

from tool_dsml import parse_dsml_tool_calls, tool_block_end, find_dsml_start
from tool_sieve import StreamSieve
from tests.test_dsml_variants import report_call,collect


@pytest.mark.parametrize('bars',['||','｜｜','|||','｜｜｜','|｜|','｜|｜|'])
def test_repeated_separator_block_end_matches_complete_parser(bars):
    text=report_call(bars=bars)
    calls,cleaned=parse_dsml_tool_calls(text)
    assert cleaned=='' and json.loads(calls[0]['function']['arguments'])=={'cmd':'echo hello'}
    assert tool_block_end(text)==len(text)


@pytest.mark.parametrize('bars',['||','｜｜','|||','｜｜｜','|｜|','｜|｜|'])
def test_repeated_separators_at_every_split_preserve_calls_and_text(bars):
    text=report_call(bars=bars)
    for split in range(len(text)+1):
        visible,calls=collect(['İ before '+text[:split],text[split:]+' after'])
        assert visible=='İ before  after' and len(calls)==1, split
        assert json.loads(calls[0]['function']['arguments'])=={'cmd':'echo hello'}
    visible,calls=collect(list(text))
    assert visible=='' and len(calls)==1


@pytest.mark.parametrize('text',['text || normal ｜｜ values','a <|| not DSML','<|||DSM','<｜｜DSML｜｜callsign>literal'])
def test_repeated_pipe_prose_and_incomplete_markers_pass_through(text):
    visible,calls=collect(list(text))
    assert visible==text and calls==[]


def test_repeated_separators_inside_cdata_are_literal_and_do_not_end_capture():
    command='echo "</｜｜DSML｜｜calls> |｜|"'
    text=report_call(bars='｜｜',value=f'<![CDATA[{command}]]>')
    visible,calls=collect(list(text))
    assert visible=='' and json.loads(calls[0]['function']['arguments'])=={'cmd':command}


def test_repeated_separator_wrapper_waits_for_complete_batch():
    first=report_call(bars='｜｜').removesuffix('</｜｜DSML｜｜calls>')
    second=report_call(bars='｜｜',value='echo second').removeprefix('<｜｜DSML｜｜calls>')
    sieve=StreamSieve(parse_fn=parse_dsml_tool_calls)
    assert sieve.feed(first)==[]
    events=sieve.feed(second)
    assert len(events)==1 and events[0].type=='tool_calls' and len(events[0].data)==2
    assert sieve.flush()==[]


def test_repeated_incomplete_prefix_respects_buffer_limit_without_losing_text():
    text='<'+('｜'*100)+' DSM'
    sieve=StreamSieve(parse_fn=parse_dsml_tool_calls,max_capture_buffer=32)
    events=[]
    for character in text:
        events.extend(sieve.feed(character))
        assert len(sieve._pending)<=32
    events.extend(sieve.flush())
    assert ''.join(event.data for event in events if event.type=='text')==text
    assert not any(event.type=='tool_calls' for event in events)


def test_repeated_marker_whitespace_and_large_width_are_not_enumerated_variants():
    text=report_call(bars='｜'*64).replace('DSML',' dsml ')
    visible,calls=collect(list(text))
    assert visible=='' and len(calls)==1 and tool_block_end(text)==len(text)


def test_long_ordinary_pipe_runs_preserve_text_and_later_real_marker():
    prose='ordinary '+('｜'*16384)+' not DSML '
    assert find_dsml_start(prose)==-1
    text=report_call(bars='｜｜')
    assert find_dsml_start(prose+text)==len(prose)
    visible,calls=collect([prose,text])
    assert visible==prose and len(calls)==1
