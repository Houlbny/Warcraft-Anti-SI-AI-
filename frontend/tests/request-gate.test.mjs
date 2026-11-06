import test from 'node:test';
import assert from 'node:assert/strict';
import {RequestGate} from '../src/requestGate.ts';
test('late results cannot cross contexts or overwrite a newer result',()=>{
 const gate=new RequestGate();gate.change('hero-a');const old=gate.issue('hero-a');gate.change('hero-b');const first=gate.issue('hero-b');const second=gate.issue('hero-b');
 assert.equal(gate.accept(old),false);assert.equal(gate.accept(second),true);assert.equal(gate.accept(first),false);
 gate.change('hero-a');assert.equal(gate.accept(old),false);assert.equal(gate.issue('hero-b'),null);
});
test('source path and workspace changes invalidate in-flight responses',()=>{
 const gate=new RequestGate();gate.change(JSON.stringify(['a','t','one',true]));const old=gate.issue(JSON.stringify(['a','t','one',true]));gate.change(JSON.stringify(['a','t','two',true]));assert.equal(gate.accept(old),false);
});
import {NavigationEpoch} from '../src/requestGate.ts';
test('deferred creation and list responses cannot reclaim navigation',async()=>{
 let resolvePost,resolveList;
 const post=new Promise(resolve=>{resolvePost=resolve});
 const list=new Promise(resolve=>{resolveList=resolve});
 const gate=new NavigationEpoch();const changes=[];
 const start=async()=>{const creation=gate.capture();await post;if(!gate.matches(creation))return;changes.push('select A');gate.advance();const selection=gate.capture();await list;if(!gate.matches(selection))return;changes.push('open A')};
 const first=start();gate.advance();resolvePost();await first;assert.deepEqual(changes,[]);
 const second=start();resolvePost();await Promise.resolve();assert.deepEqual(changes,['select A']);gate.advance();resolveList();await second;assert.deepEqual(changes,['select A']);
});
