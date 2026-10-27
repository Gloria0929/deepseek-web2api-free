import assert from 'node:assert/strict';
import fs from 'node:fs';
import OpenAI from 'openai';

const args=Object.fromEntries(process.argv.slice(2).reduce((out,arg,index,all)=>{if(arg.startsWith('--'))out.push([arg.slice(2),all[index+1]]);return out;},[]));
const probes=JSON.parse(fs.readFileSync(args.probes,'utf8'));
const client=new OpenAI({baseURL:args['base-url']+'/v1',apiKey:args['api-key'],maxRetries:0,timeout:180000});
const sleep=()=>new Promise(resolve=>setTimeout(resolve,2000));
const create=async body=>{await sleep();return client.responses.create({model:'deepseek-chat',...body});};
const state=async()=>{const response=await fetch(args['base-url']+'/__test__/state',{headers:{Authorization:'Bearer '+args['api-key']}});return response.json();};
let failed=false;
async function check(name,action){
  try{await action();console.log(JSON.stringify({name,passed:true}));}
  catch(error){failed=true;console.log(JSON.stringify({name,passed:false,failure_type:error.constructor.name,http_status:error.status??null}));}
}
await check('live_text_previous',async()=>{
  const first=await create({input:'Remember code CEDAR-904. Reply only ACK.'});assert.equal(first.output_text.trim().replace(/[.!]$/,''),'ACK');
  const second=await create({input:'Repeat the code only.',previous_response_id:first.id});assert.ok(second.output_text.toUpperCase().includes('CEDAR-904'));
});
await check('live_hidden_image_and_append',async()=>{
  const before=(await state()).uploads;
  const first=await create({input:[{role:'user',content:[{type:'input_text',text:'Inspect silently. Reply only ACK without describing the image.'},{type:'input_image',image_url:probes.red}]}]});
  assert.equal(first.output_text.trim().replace(/[.!]$/,''),'ACK');await sleep();
  const stream=client.responses.stream({model:'deepseek-chat',input:'Describe the color and shape of my previous image.',previous_response_id:first.id});
  for await(const event of stream){}
  const recalled=await stream.finalResponse();assert.ok(['red','circle'].every(w=>recalled.output_text.toLowerCase().includes(w)));
  assert.equal((await state()).uploads,before+1);assert.equal((await state()).requests.at(-1).refs,1);
  const compared=await create({input:[{role:'user',content:[{type:'input_text',text:'Compare the first image with this new image, including color, shape and count, in chronological order.'},{type:'input_image',image_url:probes.blue}]}],previous_response_id:recalled.id});
  assert.ok(['red','circle','blue','square'].every(w=>compared.output_text.toLowerCase().includes(w)));assert.equal((await state()).uploads,before+2);assert.equal((await state()).requests.at(-1).refs,2);
});
await check('live_strict_multi_function_loop',async()=>{
  const tools=[{type:'function',name:'weather',description:'Get weather of one city.',strict:true,parameters:{type:'object',properties:{city:{type:'string'}},required:['city'],additionalProperties:false}}];
  const result=await create({input:'Call weather exactly twice: once for Paris and once for London. Do not answer directly.',tools,tool_choice:'required',parallel_tool_calls:true});
  const calls=result.output.filter(i=>i.type==='function_call');assert.equal(calls.length,2);assert.equal(new Set(calls.map(i=>i.call_id)).size,2);
  assert.deepEqual(calls.map(i=>JSON.parse(i.arguments).city.toLowerCase()).sort(),['london','paris']);assert.equal(result.tools[0].strict,true);
  const final=await create({input:calls.toReversed().map((item,index)=>({type:'function_call_output',call_id:item.call_id,output:JSON.stringify({city:JSON.parse(item.arguments).city,temperature_c:19+index})})),previous_response_id:result.id,instructions:'Report both cities and both temperatures in Celsius.'});
  assert.ok(final.output_text.includes('19')&&final.output_text.includes('20'));
});
process.exitCode=failed?1:0;
