from types import SimpleNamespace

from chat_service import ChatService


def test_shared_prompt_preserves_tools_and_roles():
    text = ChatService.build_prompt([{'role': 'system', 'content': 'rules'},
        {'role': 'user', 'content': [{'type': 'text', 'text': 'hello'}]},
        {'role': 'tool', 'tool_call_id': 'call-1', 'content': 'result'}])
    assert text == 'System: rules\nUser: hello\nTool result (call_id=call-1): result'


def test_events_preserve_reasoning_and_close_raw_stream():
    closed = []
    def stream(*a, **kw):
        try:
            yield {'__type': 'thinking', 'content': 'think'}
            yield 'answer'
            yield {'__type': 'status', 'status': 'FINISHED'}
            yield 'must not leak'
        finally:
            closed.append(True)
    acquired = SimpleNamespace(adapter=SimpleNamespace(chat_stream=stream), parent_message_id=None)
    turn = SimpleNamespace(session_id='s', prompt='p', completion_options=lambda: {})
    events = list(ChatService.events(acquired, turn, {}, [], {}))
    assert [(e.kind, e.text) for e in events] == [('reasoning', 'think'), ('text', 'answer')]
    assert closed == [True]
