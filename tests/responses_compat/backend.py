"""Scripted upstream only. Real routes, parser, pool and image preparation run."""
import threading
import time
from pathlib import Path

from adapter import RateLimitError
from image_inputs import ImageError


class ScriptedAdapter:
    def __init__(self):
        self.files = self
        self.lock = threading.RLock()
        self.active = self.closed = self.uploads = self.sessions = 0
        self.prepare_delay = 0
        self.requests = []

    def create_session(self):
        time.sleep(self.prepare_delay)
        with self.lock:
            self.sessions += 1
            return f'synthetic-session-{self.sessions}'

    def upload(self, asset, **kwargs):
        if asset.width == 13: raise ImageError('Synthetic processing failure',502)
        with self.lock:
            self.uploads += 1
            return f'synthetic-file-{self.uploads}'

    def wait_ready(self, ids): pass

    def chat(self, session, prompt, **options):
        text, thinking = [], []
        for token in self.chat_stream(session,prompt,**options):
            if isinstance(token,str): text.append(token)
            elif token.get('__type') == 'thinking': thinking.append(token['content'])
        return ''.join(text), ''.join(thinking)

    def chat_stream(self, session, prompt, **options):
        last = prompt.split('\nUser: ')[-1]
        if prompt.rfind('Tool result (call_id=') > prompt.rfind('User: '): last = 'case:tool_result'
        refs = options.get('ref_file_ids',[])
        with self.lock:
            self.active += 1
            self.requests.append({'refs':len(refs),'session':session,'has_parent':options.get('parent_message_id') is not None})
        try:
            if 'case:rate' in last: raise RateLimitError('Synthetic private limit detail')
            if 'case:upstream_fail' in last: raise RuntimeError('Synthetic private failure')
            if 'case:slow_first' in last: time.sleep(.8)
            if 'case:codex_tool' in prompt:
                if 'Tool result (call_id=' in prompt:
                    text = 'CODEX-PROBE' if 'CODEX-PROBE' in prompt.split('Tool result (call_id=')[-1] else 'missing probe'
                else:
                    if 'case:codex_tool_variant' in prompt:
                        text = '<｜｜DSML｜｜calls><｜｜DSML｜｜invoke name="shell_command"><｜｜DSML｜｜parameter name="command" string="true">cat probe.txt</｜｜DSML｜｜parameter></｜｜DSML｜｜invoke></｜｜DSML｜｜calls>'
                    else:
                        text = '<tool_calls><invoke name="shell_command"><parameter name="command">cat probe.txt</parameter></invoke></tool_calls>'
            elif 'case:batch' in last or 'case:slow_tool' in last:
                second = '123' if 'invalid' in last else 'London'
                text = (Path(__file__).parent/'fixtures/two-calls.txt').read_text(encoding='utf-8').strip().replace('London',second)
            elif 'case:later_invalid' in last:
                yield '<tool_calls><invoke name="weather"><parameter name="city">Paris</parameter></invoke></tool_calls>'
                text = '<tool_calls><invoke name="weather"><parameter name="city">123</parameter></invoke></tool_calls>'
            elif 'Tool result (call_id=' in prompt and prompt.rfind('Tool result (call_id=') > prompt.rfind('User: '):
                text = 'tool results received'
            elif 'case:image_compare' in last: text = 'red circle; two blue squares' if len(refs) == 2 else 'missing image'
            elif 'case:image_recall' in last: text = 'red circle' if refs else 'missing image'
            elif 'case:image_ack' in last: text = 'ACK' if refs else 'missing image'
            elif 'case:code_recall' in last: text = 'CEDAR-904' if 'CEDAR-904' in prompt else 'missing code'
            elif 'case:slow_text' in last:
                yield 'hello'
                time.sleep(.8)
                text = ' world'
            else: text = 'answer'
            yield {'__type':'thinking','content':'synthetic reasoning'}
            # Split DSML across chunks; parser/sieve must preserve complete batches.
            if 'case:codex_tool_variant' in prompt:
                yield from text  # Stress every tag/attribute boundary.
            else:
                midpoint = max(1,len(text)//2)
                yield text[:midpoint]; yield text[midpoint:]
            if 'case:slow_tool' in last: time.sleep(.8)
            if 'case:midstream_fail' in last: raise RuntimeError('Synthetic private midstream detail')
            options.get('ready_out',{}).update(response_message_id=2,session_id=session)
            yield {'__type':'status','status':'FINISHED'}
        finally:
            with self.lock:
                self.active -= 1; self.closed += 1
