import test from 'node:test';
import assert from 'node:assert/strict';
import {api,ApiError} from '../src/api.ts';

test('a journal revision conflict retains HTTP 409 for explicit reconciliation',async t=>{
 t.mock.method(globalThis,'fetch',async()=>new Response(JSON.stringify({detail:'日记版本已变化'}),{status:409}));
 await assert.rejects(api('/projects/test/journal/q1','PATCH',{revision:2,self_assessment:'review',note:'需要复习'}),error=>error instanceof ApiError&&error.status===409&&error.message==='日记版本已变化');
});

test('a failed request is sent once and never automatically repeated by the frontend API',async t=>{
 const fetch=t.mock.method(globalThis,'fetch',async()=>{throw new TypeError('connection interrupted')});
 await assert.rejects(api('/projects/test/campaign/next','POST',{}),/connection interrupted/);
 assert.equal(fetch.mock.callCount(),1);
});
