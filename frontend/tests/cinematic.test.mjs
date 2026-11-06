import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

// Execute the actual component with a deterministic hook/DOM clock. This exercises
// timeout cleanup and navigation without adding a DOM library to production.
function harness({ reduced = false } = {}) {
  let hookIndex = 0, effects = [], tree, now = 0, nextTimer = 0, dirty = false, finished = 0;
  const hooks = [], timers = new Map(), listeners = new Map(), mediaListeners = new Set();
  class Element { focus() {} }
  class Button extends Element {}
  const media = { matches: reduced, addEventListener: (_,f) => mediaListeners.add(f), removeEventListener: (_,f) => mediaListeners.delete(f) };
  const document = { hidden: false, activeElement: new Element(), addEventListener: (key,fn) => listeners.set(key,fn), removeEventListener: (key,fn) => { if (listeners.get(key) === fn) listeners.delete(key); } };
  const window = { matchMedia: () => media, setTimeout: (fn,ms) => { const id=++nextTimer; timers.set(id,{fn,at:now+ms}); return id; }, clearTimeout: id => timers.delete(id) };
  const equal = (a,b) => a && b && a.length === b.length && a.every((v,i) => Object.is(v,b[i]));
  const react = {
    useState(initial) { const i=hookIndex++; if (!(i in hooks)) hooks[i]={value:typeof initial==='function'?initial():initial}; return [hooks[i].value,v => { hooks[i].value=typeof v==='function'?v(hooks[i].value):v; dirty=true; }]; },
    useRef(value) { const i=hookIndex++; if (!(i in hooks)) hooks[i]={current:value}; return hooks[i]; },
    useCallback(fn,deps) { const i=hookIndex++; if (!hooks[i] || !equal(hooks[i].deps,deps)) hooks[i]={value:fn,deps}; return hooks[i].value; },
    useEffect(fn,deps) { const i=hookIndex++; if (!hooks[i] || !equal(hooks[i].deps,deps)) { effects.push(() => { hooks[i]?.cleanup?.(); hooks[i]={deps,cleanup:fn()}; }); } },
  };
  const compile = source => ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
  const story = { exports:{} };
  vm.runInNewContext(compile(readFileSync(new URL('../src/story.ts',import.meta.url),'utf8')), { exports:story.exports });
  const component = { exports:{} };
  const jsx = (type,props) => ({type,props});
  vm.runInNewContext(compile(readFileSync(new URL('../src/IntroCinematic.tsx',import.meta.url),'utf8')), {
    exports:component.exports,
    require: name => name==='react'?react:name==='react/jsx-runtime'?{jsx,jsxs:jsx}:name==='./story'?story.exports:{},
    window,document,HTMLElement:Element,HTMLButtonElement:Button,performance:{now:()=>now},
  });
  const onComplete = () => finished++;
  function render() { do { dirty=false; hookIndex=0; tree=component.exports.default({onComplete}); const pending=effects; effects=[]; pending.forEach(f=>f()); } while(dirty); }
  const nodes = (node,predicate) => !node||typeof node!=='object'?[]:[...(predicate(node)?[node]:[]),...[node.props?.children].flat(Infinity).flatMap(child=>nodes(child,predicate))];
  render();
  return {
    get finished() { return finished; }, get timerCount() { return timers.size; },
    title: () => nodes(tree,n=>n.type==='h1')[0].props.children,
    click(text) { const button=nodes(tree,n=>n.type==='button' && [n.props.children].flat(Infinity).includes(text))[0]; assert.ok(button,`button ${text}`); button.props.onClick(); render(); },
    key(key) { listeners.get('keydown')?.({key,preventDefault(){},target:document.activeElement}); render(); },
    advance(ms) { const end=now+ms; while(true) { const due=[...timers].filter(([,v])=>v.at<=end).sort((a,b)=>a[1].at-b[1].at)[0]; if(!due)break; now=due[1].at;timers.delete(due[0]);due[1].fn();render(); } now=end; },
    hide(value) { document.hidden=value; listeners.get('visibilitychange')?.(); render(); },
    reduce(value) { media.matches=value; mediaListeners.forEach(fn=>fn()); render(); },
    unmount() { hooks.forEach(h=>h?.cleanup?.()); },
    get listenerCount() { return listeners.size+mediaListeners.size; },
  };
}

test('three paced scenes complete once and remove timers/listeners on unmount', () => {
  const app=harness();
  assert.equal(app.title(),'第十三次钟声');
  app.advance(9999); assert.equal(app.title(),'第十三次钟声');
  app.advance(1); assert.equal(app.title(),'第一次反抗失败了');
  app.advance(10000); assert.equal(app.title(),'从营地重新出发');
  app.advance(12000); assert.equal(app.finished,1);
  app.key('Escape'); assert.equal(app.finished,1);
  app.unmount(); assert.equal(app.timerCount,0); assert.equal(app.listenerCount,0);
});

test('pause and background visibility preserve the remaining scene time', () => {
  const app=harness();
  app.advance(4000); app.click('暂停播放'); assert.equal(app.timerCount,0);
  app.advance(20000); assert.equal(app.title(),'第十三次钟声');
  app.click('继续播放'); app.advance(3000); app.hide(true); assert.equal(app.timerCount,0);
  app.advance(20000); app.hide(false); app.advance(2999); assert.equal(app.title(),'第十三次钟声');
  app.advance(1); assert.equal(app.title(),'第一次反抗失败了'); app.unmount();
});

test('reduced motion starts in manual mode and responds to preference changes', () => {
  const app=harness({reduced:true});
  assert.equal(app.timerCount,0); app.advance(60000); assert.equal(app.title(),'第十三次钟声');
  app.key('ArrowRight'); assert.equal(app.title(),'第一次反抗失败了');
  app.key('ArrowLeft'); assert.equal(app.title(),'第十三次钟声');
  app.key('Enter'); assert.equal(app.title(),'第一次反抗失败了');
  app.key(' '); assert.equal(app.timerCount,1);
  app.reduce(true); assert.equal(app.timerCount,0);
  app.key('Escape'); assert.equal(app.finished,1); app.unmount();
});
