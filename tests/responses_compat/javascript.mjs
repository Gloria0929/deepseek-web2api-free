import assert from 'node:assert/strict';
import fs from 'node:fs';
import OpenAI from 'openai';

const args = Object.fromEntries(process.argv.slice(2).reduce((pairs, value, index, all) => {
  if (value.startsWith('--')) pairs.push([value.slice(2), all[index + 1]]);
  return pairs;
}, []));
const client = new OpenAI({baseURL: args['base-url'] + '/v1', apiKey: args['api-key'], maxRetries: 0});
const probes = JSON.parse(fs.readFileSync(new URL('./fixtures/images.json', import.meta.url), 'utf8'));
const tools = [{type: 'function', name: 'weather', parameters: {type: 'object', properties: {city: {type: 'string'}}}}];
const create = (body, options) => client.responses.create({model: 'deepseek-chat', ...body}, options);
const visible = response => response.output.filter(i => i.type === 'message').flatMap(i => i.content).filter(p => p.type === 'output_text').map(p => p.text).join('');
async function control(path, body) {
  const response = await fetch(args['base-url'] + '/__test__/' + path, {
    method: body === undefined ? 'GET' : 'POST', headers: {'Authorization': 'Bearer ' + args['api-key'], 'Content-Type': 'application/json'},
    body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(3000),
  });
  assert.equal(response.status, 200); return response.json();
}
const state = () => control('state');
const mode = value => control('mode', {mode: value});
async function idle(stored) {
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    const current = await state();
    if (current.active === 0 && current.busy === 0) { if (stored !== undefined) assert.equal(current.stored, stored); return; }
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  throw new Error('Account/worker did not drain');
}
async function status(expected, body, options) {
  await assert.rejects(() => create(body, options), error => error.status === expected);
}
async function check(name, action) {
  try { await action(); console.log(JSON.stringify({name, client: 'javascript', passed: true})); }
  catch (error) { console.log(JSON.stringify({name, client: 'javascript', passed: false})); throw error; }
}

await check('text', async () => {
  const result = await create({input: 'case:text', metadata: null, tool_choice: null});
  assert.equal(result.output_text, 'answer'); assert.equal(result.status, 'completed');
});
await check('stream', async () => {
  const stream = await create({input: 'case:text', stream: true}); const seen = [];
  for await (const event of stream) seen.push(event);
  assert.equal(seen.at(-1).type, 'response.completed'); assert.equal(visible(seen.at(-1).response), 'answer');
  assert.deepEqual(seen.map(e => e.sequence_number), seen.map((_, index) => index));
});
await check('aggregate', async () => {
  const stream = client.responses.stream({model: 'deepseek-chat', input: 'case:text'});
  for await (const event of stream) { /* Exercise the official aggregator. */ }
  assert.equal((await stream.finalResponse()).output_text, 'answer');
});
await check('images', async () => {
  const before = (await state()).uploads;
  const first = await create({input: [{role: 'user', content: [{type: 'input_text', text: 'case:image_ack'}, {type: 'input_image', image_url: probes.red}]}]});
  assert.equal(first.output_text, 'ACK');
  const recalled = await create({input: 'case:image_recall', previous_response_id: first.id});
  assert.equal(recalled.output_text, 'red circle'); assert.equal((await state()).uploads, before + 1);
  const compared = await create({input: [{role: 'user', content: [{type: 'input_text', text: 'case:image_compare'}, {type: 'input_image', image_url: probes.blue}]}], previous_response_id: recalled.id});
  assert.ok(compared.output_text.includes('blue squares')); assert.equal((await state()).uploads, before + 2);
  await status(502, {input: [{role: 'user', content: [{type: 'input_image', image_url: probes.failure}]}], stream: true});
});
await check('state_and_replay', async () => {
  const first = await create({input: 'Remember CEDAR-904', instructions: 'old rule'});
  const branch = await create({input: 'case:code_recall', previous_response_id: first.id, instructions: 'new rule'});
  assert.equal(branch.output_text, 'CEDAR-904'); assert.equal((await client.responses.retrieve(branch.id)).output_text, branch.output_text);
  await client.responses.delete(branch.id);
  await assert.rejects(() => client.responses.retrieve(branch.id), error => error.status === 404);
  const manual = await create({input: 'case:text', store: false});
  await assert.rejects(() => client.responses.retrieve(manual.id), error => error.status === 404);
  const replay = await create({input: [...manual.output, {role: 'user', content: 'case:text'}], store: false});
  assert.equal(replay.output_text, 'answer');
});
await check('tools', async () => {
  const stream = client.responses.stream({model: 'deepseek-chat', input: 'case:batch', tools, parallel_tool_calls: true, tool_choice: 'required'});
  for await (const event of stream) { /* Accumulate both calls. */ }
  const result = await stream.finalResponse(), calls = result.output.filter(i => i.type === 'function_call');
  assert.equal(calls.length, 2); assert.equal(new Set(calls.map(i => i.call_id)).size, 2); assert.equal(result.tools[0].strict, true);
  const outputs = calls.toReversed().map(i => ({type: 'function_call_output', call_id: i.call_id, output: 'sunny'}));
  assert.equal((await create({input: [...calls, ...outputs], store: false})).output_text, 'tool results received');
  const before = (await state()).uploads;
  outputs[0].output = [{type: 'input_text', text: 'screenshot'}, {type: 'input_image', image_url: probes.blue}];
  assert.equal((await create({input: outputs, previous_response_id: result.id})).output_text, 'tool results received');
  assert.equal((await state()).uploads, before + 1); assert.equal((await state()).requests.at(-1).refs, 1);
  await status(400, {input: outputs.slice(0, 1), previous_response_id: result.id});
  await status(400, {input: [outputs[0], outputs[0]], previous_response_id: result.id});
  await status(502, {input: 'case:batch', tools});
});
await check('errors', async () => {
  await status(400, {input: 'case:text', max_output_tokens: 20});
  await status(401, {input: 'x'}, {headers: {Authorization: 'Bearer invalid'}});
  await status(404, {input: 'x', previous_response_id: 'resp_missing'});
  await status(429, {input: 'case:rate'}); await status(502, {input: 'case:upstream_fail'});
  await mode('busy'); try { await status(503, {input: 'x'}); } finally { await mode('idle'); }
  const parent = await create({input: 'case:text'}); await mode('credentials');
  await status(409, {input: 'x', previous_response_id: parent.id});
});
await check('failures', async () => {
  for (const [input, options] of [['case:batch invalid', {tools, parallel_tool_calls: true}], ['case:midstream_fail', {}], ['case:rate', {}], ['case:later_invalid', {tools, parallel_tool_calls: true}]]) {
    const before = (await state()).stored, stream = await create({input, stream: true, ...options}), seen = [];
    for await (const event of stream) seen.push(event);
    assert.equal(seen.filter(e => e.type === 'response.failed').length, 1); assert.ok(!seen.some(e => e.type === 'response.completed'));
    if (input === 'case:batch invalid') assert.ok(!seen.some(e => e.type === 'response.output_item.added' && e.item.type === 'function_call'));
    if (input === 'case:rate') assert.equal(seen.at(-1).response.error.code, 'rate_limit_exceeded');
    assert.equal((await state()).stored, before);
  }
});
await check('abort', async () => {
  for (const [input, checkpoint] of [['case:slow_first', 'response.created'], ['case:slow_text', 'response.output_text.delta'], ['case:slow_tool', 'response.function_call_arguments.delta']]) {
    const before = (await state()).stored, stream = await create({input, stream: true, tools, parallel_tool_calls: true});
    let observed = false;
    try { for await (const event of stream) { if (event.type === checkpoint) { observed = true; break; } } }
    finally { stream.controller.abort(); }
    assert.ok(observed); await idle(before); assert.equal((await create({input: 'case:text'})).output_text, 'answer');
  }
});
await check('timeout', async () => {
  const before = (await state()).stored; await mode('slow_prepare');
  try { await assert.rejects(() => create({input: 'case:text', stream: true}, {timeout: 150}), error => error instanceof OpenAI.APIConnectionTimeoutError); }
  finally { await mode('normal'); }
  await idle(before); assert.equal((await create({input: 'case:text'})).output_text, 'answer');
  for (const input of ['case:slow_text', 'case:slow_tool']) {
    const count = (await state()).stored, signal = AbortSignal.timeout(150);
    const stream = await create({input, stream: true, tools, parallel_tool_calls: true}, {signal});
    let completed = false;
    try { for await (const event of stream) { completed ||= event.type === 'response.completed'; } }
    catch (error) { assert.ok(signal.aborted); }
    finally { stream.controller.abort(); }
    assert.ok(signal.aborted && !completed); await idle(count);
    assert.equal((await create({input: 'case:text'})).output_text, 'answer');
  }
});
