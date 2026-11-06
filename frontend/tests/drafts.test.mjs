import test from 'node:test';
import assert from 'node:assert/strict';
import {draftKey,clearSavedDraft} from '../src/drafts.ts';
test('drafts remain isolated across adventures and tasks, including separator characters',()=>{
 const keys=[draftKey('adventure-a','task-1'),draftKey('adventure-a','task-2'),draftKey('adventure-b','task-1'),draftKey('a:b','c'),draftKey('a','b:c')];
 assert.equal(new Set(keys).size,keys.length);
 assert.equal(draftKey('adventure-a','task-1'),keys[0]);
});

test('a save cannot discard a newer draft',()=>{
 const data=new Map([['file','new edit']]);const storage={getItem:k=>data.get(k)??null,removeItem:k=>data.delete(k)};
 clearSavedDraft(storage,'file','saved snapshot');assert.equal(data.get('file'),'new edit');
 clearSavedDraft(storage,'file','new edit');assert.equal(data.has('file'),false);
});
