import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const compiled=ts.transpileModule(readFileSync(new URL('../src/App.tsx',import.meta.url),'utf8'),{
  compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022},
}).outputText;

function harness(componentName,props,{storage=new Map()}={}){
  let index=0,dirty=false,tree;
  const hooks=[],effects=[],requests=[];
  const localStorage={getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)};
  const same=(a,b)=>a&&b&&a.length===b.length&&a.every((v,i)=>Object.is(v,b[i]));
  const react={
    useState(initial){const i=index++;if(!(i in hooks))hooks[i]={value:typeof initial==='function'?initial():initial};return[hooks[i].value,value=>{hooks[i].value=typeof value==='function'?value(hooks[i].value):value;dirty=true}]},
    useRef(value){const i=index++;if(!(i in hooks))hooks[i]={current:value};return hooks[i]},
    useEffect(fn,deps){const i=index++;if(!hooks[i]||!same(hooks[i].deps,deps))effects.push(()=>{hooks[i]?.cleanup?.();hooks[i]={deps,cleanup:fn()}})},
  };
  const api=(url,method,data)=>new Promise((resolve,reject)=>requests.push({url,method,data,resolve,reject}));
  const exports={};const jsx=(type,props)=>({type,props});
  vm.runInNewContext(compiled,{exports,localStorage,require:name=>name==='react'?react:name==='react/jsx-runtime'?{jsx,jsxs:jsx}:name==='./api'?{api}:name==='./drafts'?{clearSavedDraft:(store,key,saved)=>{if(store.getItem(key)===saved)store.removeItem(key)}}:{}});
  function render(){do{dirty=false;index=0;tree=exports[componentName](props);effects.splice(0).forEach(fn=>fn())}while(dirty)}
  const nodes=(node,accept)=>!node||typeof node!=='object'?[]:[...(accept(node)?[node]:[]),...[node.props?.children].flat(Infinity).flatMap(child=>nodes(child,accept))];
  const content=node=>node==null||typeof node==='boolean'?'':typeof node==='object'?[node.props?.children].flat(Infinity).map(content).join(''):String(node);
  render();
  return{
    requests,storage,
    text:()=>content(tree),
    click(label){const node=nodes(tree,n=>n.type==='button'&&content(n)===label)[0];assert.ok(node,`button ${label}`);const result=node.props.onClick();render();return result},
    note(value){nodes(tree,n=>n.type==='textarea')[0].props.onChange({target:{value}});render()},
    noteValue:()=>nodes(tree,n=>n.type==='textarea')[0].props.value,
    confidence(value){nodes(tree,n=>n.type==='select')[0].props.onChange({target:{value}});render()},
    async resolve(value){requests[0].resolve(value);await new Promise(resolve=>setImmediate(resolve));render()},
    unmount(){hooks.forEach(h=>h?.cleanup?.())},
  };
}

test('recall starts without exposing teaching and learning is an explicit choice',()=>{
  const lesson={title:'原理讲解',body:'这是讲解中的答案线索'};
  const app=harness('LearningPath',{teaching:[lesson]});
  assert.equal(app.text().includes(lesson.body),false);
  app.click('先尝试回忆');
  assert.equal(app.text().includes(lesson.body),false);
  app.click('查看讲解');assert.equal(app.text().includes(lesson.body),true);
  app.unmount();
  const next=harness('LearningPath',{teaching:[lesson]});
  assert.equal(next.text().includes(lesson.body),false);
  next.click('先学习理解');assert.equal(next.text().includes(lesson.body),true);
});

test('saving reflection preserves newer edits and sends the owning task only',async()=>{
  const app=harness('ReflectionEditor',{adventureId:'a',taskId:'t',onSaved:async()=>{}});
  app.note('已保存的笔记');app.confidence('understood');
  app.click('保存自评');
  assert.equal(app.requests[0].url,'/adventures/a/tasks/t/reflection');
  assert.equal(app.requests[0].data.note,'已保存的笔记');
  app.note('提交后继续写的新笔记');
  await app.resolve({confidence:'understood',note:'已保存的笔记',updated_at:'now'});
  assert.equal(app.noteValue(),'提交后继续写的新笔记');
  assert.ok([...app.storage.values()].some(value=>value.includes('提交后继续写的新笔记')));
  app.unmount();
});

test('leaving a reflection save cannot refresh another task or erase its draft',async()=>{
  const storage=new Map();let refreshes=0;
  const first=harness('ReflectionEditor',{adventureId:'a',taskId:'first',onSaved:async()=>{refreshes++}},{storage});
  first.note('任务一笔记');first.click('保存自评');first.unmount();
  const second=harness('ReflectionEditor',{adventureId:'a',taskId:'second',onSaved:async()=>{refreshes++}},{storage});
  second.note('任务二笔记');
  await first.resolve({confidence:'needs_review',note:'任务一笔记',updated_at:'now'});
  assert.equal(refreshes,0);
  assert.equal(second.noteValue(),'任务二笔记');
  assert.ok([...storage.values()].some(value=>value.includes('任务二笔记')));
  second.unmount();
});

test('reflection-only journal entries do not imply an AI review was requested',()=>{
  const exports={};
  vm.runInNewContext(compiled,{exports,require:()=>({})});
  const reflection={confidence:'understood',note:'已经读懂流程',updated_at:'now'};
  assert.equal(exports.journalEntryLabel({status:'pending',reflection}),'自评记录');
  assert.equal(exports.journalEntryLabel({status:'pending',answer:'我的理解',reflection}),'等待评审');
  assert.equal(exports.journalEntryLabel({status:'passed',answer:'我的理解',reflection}),'本次已通过');
});
