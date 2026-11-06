import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

function render(record, overrides={}){
  const compiled=ts.transpileModule(readFileSync(new URL('../src/MaterialAnalysis.tsx',import.meta.url),'utf8'),{
    compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022},
  }).outputText;
  const exports={};
  const jsx=(type,props)=>typeof type==='function'?type(props):({type,props});
  vm.runInNewContext(compiled,{exports,require:name=>name==='react/jsx-runtime'?{jsx,jsxs:jsx}:{}});
  let calls=0;
  const tree=exports.default({record,focus:'',setFocus(){},busy:false,onAnalyze(){calls++},onStart(){},...overrides});
  const nodes=(node,accept)=>!node||typeof node!=='object'?[]:[...(accept(node)?[node]:[]),...[node.props?.children].flat(Infinity).flatMap(child=>nodes(child,accept))];
  const content=node=>node==null||typeof node==='boolean'?'':typeof node==='object'?[node.props?.children].flat(Infinity).map(content).join(''):String(node);
  return{exports,text:content(tree),buttons:nodes(tree,node=>node.type==='button'),inputs:nodes(tree,node=>node.type==='input'),progress:nodes(tree,node=>node.type==='progress'),content,get calls(){return calls}};
}

const analysis={summary:'上次分析的旧结果',content_type:'文档',recommendations:[{role:'mage',reason:'解释文档概念',focus:'概念关系'}]};
const base={id:'i',title:'大材料',files:[],diagnostics:[],status:'imported',analysis_complete:false,coverage:{selected_chars:212845,total_chars:212845,context_chars:10608,truncated:true,batch_count:23,max_batch_chars:10608}};
const progress={phase:'reading',total_batches:23,completed_batches:3,failed_batches:0,processed_chars:30000,total_chars:212845,current_batch:4};

test('large imports describe automatic complete-scope batching and never start analysis by rendering',()=>{
  const view=render(base);
  assert.match(view.text,/212,?845/);
  assert.match(view.text,/预计 23 批，实际批次数会按请求大小自动调整/);
  assert.match(view.text,/自动分批/);
  assert.equal(view.inputs[0].props.maxLength,2000);
  assert.doesNotMatch(view.text,/截断|缩小.*范围/);
  assert.equal(view.calls,0);
  view.buttons.find(button=>view.content(button)==='分析材料').props.onClick();
  assert.equal(view.calls,1);
});

test('reading progress reports real batches and hides stale role recommendations',()=>{
  const view=render({...base,status:'analyzing',analysis,analysis_progress:{...progress,total_batches:26}});
  assert.match(view.text,/3 \/ 26 批/);
  assert.match(view.text,/共 26 批/);
  assert.doesNotMatch(view.text,/23 批|预计/);
  assert.match(view.text,/30,?000/);
  assert.equal(view.progress[0].props.max,26);
  assert.equal(view.progress[0].props.value,3);
  assert.doesNotMatch(view.text,/上次分析的旧结果|使用法师探索/);
  assert.equal(view.buttons.find(button=>view.content(button)==='分析中…').props.disabled,true);
});

test('all raw batches finished is not success before global synthesis completes',()=>{
  const view=render({...base,status:'analyzing',analysis,analysis_progress:{...progress,phase:'summarizing',completed_batches:23,processed_chars:212845}});
  assert.match(view.text,/正在汇总/);
  assert.doesNotMatch(view.text,/全量分析完成|使用法师探索/);
  assert.equal(view.exports.analysisReady({...base,status:'analyzed',analysis}),false);
});

test('failed synthesis offers resume while keeping recommendations unavailable',()=>{
  const view=render({...base,status:'failed',analysis,analysis_progress:{...progress,phase:'failed',completed_batches:23,processed_chars:212845}});
  assert.match(view.text,/继续分析/);
  assert.match(view.text,/已保存/);
  assert.match(view.text,/汇总.*未完成/);
  assert.doesNotMatch(view.text,/使用法师探索|全量分析完成/);
});

test('only explicit completed analysis opens role choice and identifies legacy incomplete analysis',()=>{
  const ready=render({...base,status:'analyzed',analysis_complete:true,analysis,analysis_progress:{...progress,phase:'complete',completed_batches:23,processed_chars:212845}});
  assert.match(ready.text,/全量分析完成/);
  assert.match(ready.text,/使用法师探索/);
  const old=render({...base,status:'analyzed',analysis,analysis_complete:undefined});
  assert.doesNotMatch(old.text,/使用法师探索/);
  assert.match(old.text,/重新分析/);
});
