import {useEffect,useRef,useState} from 'react';
import Editor,{loader} from '@monaco-editor/react';
import * as monaco from 'monaco-editor';
import EditorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker';
import TypeScriptWorker from 'monaco-editor/esm/vs/language/typescript/ts.worker?worker';
import {Save,Play,Square,GitCompareArrows,Sparkles,Undo2,Terminal,Lightbulb,ArrowUpRight,Check,BookOpen,Code2} from 'lucide-react';
import {api,runStatus,type Project,type Quest,type FileInfo,type Source,type Run,type Evaluation} from './api';
import SourceDiff from './SourceDiff';
import {Modal} from './Panels';
self.MonacoEnvironment={getWorker:(_id,label)=>label==='typescript'||label==='javascript'?new TypeScriptWorker():new EditorWorker()};loader.config({monaco});
// Monaco 0.52 word-highlighter delays reject without a cancellation handler during model teardown.
// Keep optional occurrence highlights disabled on these frequently switched editors; other errors remain observable.
const draftKey=(pid:string,path:string)=>`codewarden.draft.${pid}.${path}`;
export default function Workbench({open,project,quest,onClose,onPassed,onError,onLibrary}:{open:boolean;project:Project;quest:Quest|null;onClose:()=>void;onPassed:(q:Quest,feedback:string)=>Promise<void>;onError:(s:string)=>void;onLibrary:(query?:string)=>void}){
 const[files,setFiles]=useState<FileInfo[]>([]),[source,setSource]=useState<Source|null>(null),[content,setContent]=useState(''),[diff,setDiff]=useState(false),[busy,setBusy]=useState(''),[message,setMessage]=useState(''),[answer,setAnswer]=useState(''),[hints,setHints]=useState<string[]>([]),[used,setUsed]=useState(quest?.hints_used||0),[evaluation,setEvaluation]=useState<Evaluation|null>(null),[runs,setRuns]=useState<Run[]>([]),[repairLog,setRepairLog]=useState(''),[runSnapshot,setRunSnapshot]=useState(''),[runPath,setRunPath]=useState(''),[conflict,setConflict]=useState(false),[stepIndex,setStepIndex]=useState(0),[runQuestId,setRunQuestId]=useState('');
 const editor=useRef<monaco.editor.IStandaloneCodeEditor|null>(null),request=useRef(0),answerInput=useRef<HTMLTextAreaElement>(null),answerPanel=useRef<HTMLElement>(null);const pid=project.id,dirty=!!source&&content!==source.content;
 const sameSource=!!quest&&source?.path===quest.source_file,canGrade=!!quest&&quest.status==='available',mode=quest?.verification_mode,textQuestion=mode==='answer'||mode==='explain';
 useEffect(()=>{let active=true;setSource(null);setFiles([]);setRuns([]);Promise.all([api<FileInfo[]>(`/projects/${pid}/files`),api<Run[]>(`/projects/${pid}/runs`)]).then(([fs,rs])=>{if(!active)return;setFiles(fs);setRuns(rs);if(!quest?.source_file&&fs[0])void loadFile(fs[0].path)}).catch(e=>onError(e.message));return()=>{active=false;request.current++}},[pid]);
 useEffect(()=>{setAnswer('');setStepIndex(0);setHints([]);setEvaluation(null);setUsed(quest?.hints_used||0);setRunSnapshot('');setRunPath('');setRunQuestId('');setMessage('');setDiff(false);if(quest?.source_file)void loadFile(quest.source_file)},[pid,quest?.id]);
 useEffect(()=>{const warn=(e:BeforeUnloadEvent)=>{if(dirty){e.preventDefault();e.returnValue=''}};window.addEventListener('beforeunload',warn);return()=>window.removeEventListener('beforeunload',warn)},[dirty]);
 async function act(name:string,fn:()=>Promise<void>){setBusy(name);setMessage('');try{await fn()}catch(e){const msg=(e as Error).message;setMessage(msg);onError(msg)}finally{setBusy('')}}
 async function loadFile(path:string,force=false){const token=++request.current;try{const s=await api<Source>(`/projects/${pid}/file?path=${encodeURIComponent(path)}`);if(token!==request.current)return;let draft:null|{content:string;hash:string}=null;try{draft=JSON.parse(localStorage.getItem(draftKey(pid,path))||'null')}catch{}if(force){if(draft)localStorage.setItem(draftKey(pid,path)+'.backup',JSON.stringify(draft));localStorage.removeItem(draftKey(pid,path));draft=null}setConflict(!!draft&&draft.hash!==s.hash);setSource({...s,hash:draft?.hash||s.hash});setContent(draft?.content??s.content);setDiff(false)}catch(e){onError((e as Error).message)}}
 function edit(value:string|undefined){const text=value??'';setContent(text);setEvaluation(null);if(source)localStorage.setItem(draftKey(pid,source.path),JSON.stringify({content:text,hash:source.hash}))}
 function invalidateRunEvidence(){setRunQuestId('');setRunSnapshot('');setRunPath('')}
 async function save(){if(!source)return;const saved=await api<{path:string;content:string;hash:string}>(`/projects/${pid}/file`,'PUT',{path:source.path,content,expected_hash:source.hash});invalidateRunEvidence();setSource({...source,...saved});localStorage.removeItem(draftKey(pid,source.path));setConflict(false);setMessage('已保存到练习副本')}
 async function run(forQuest=true){if(!source)return;await act('本机运行中',async()=>{if(dirty)await save();const grading=forQuest&&!!quest&&canGrade;const payload:{entry:string;quest_id?:string}={entry:forQuest&&quest?(quest.run_entry||quest.source_file):source.path};if(grading)payload.quest_id=quest!.id;const result=await api<Run>(`/projects/${pid}/run`,'POST',payload);setRuns(rs=>[result,...rs].slice(0,15));setRunSnapshot(content);setRunPath(source.path);setRunQuestId(grading?quest!.id:'');if(result.quest_completed&&quest)await onPassed(quest,quest.success_message||'当前输出满足验收标准。');else if(result.status==='passed'&&grading&&mode==='review')setMessage('本机运行通过。下一步：提交当前源码差异给 AI 按本关标准评审。');else if(result.status==='passed'&&grading&&mode==='run')setMessage('运行成功，但未满足本关验收。请检查左侧验收标准与实际修改后重试。');else if(!forQuest)setMessage('普通文件运行：本次不验收当前关卡。');else if(result.status!=='passed')setMessage('运行未通过，当前关卡尚未完成。查看下方实际输出，按本关标准调整后重试。')})}
 async function submit(){if(!quest)return;await act(mode==='answer'?'校验答案中':'AI 评审中',async()=>{if(mode==='review'&&dirty){await save();setMessage('源码已保存，请重新运行本关后再提交评审。');return}try{const r=await api<Evaluation>(`/projects/${pid}/quests/${quest.id}/${mode==='answer'?'answer':'review'}`,'POST',mode==='review'?{}:{answer});setEvaluation(r);if(r.correct)await onPassed(quest,r.feedback)}catch(error){const message=error instanceof Error?error.message:String(error);if(mode==='review'&&/请先运行|重新运行|源码发生变化|还没有修改目标源码/.test(message))invalidateRunEvidence();throw error}})}
 async function repair(undo=false){await act(undo?'撤销修复中':'AI 环境修复中',async()=>{if(dirty)await save();const result=await api<{message:string}>(`/projects/${pid}/${undo?'undo-repair':'repair'}`,'POST',{});setRepairLog(JSON.stringify(result,null,2));setMessage(result.message);if(source)await loadFile(source.path,true);setFiles(await api<FileInfo[]>(`/projects/${pid}/files`));invalidateRunEvidence()})}
 if(!open)return null;
 const reviewReady=runs[0]?.status==='passed'&&runSnapshot===content&&runPath===source?.path&&runQuestId===quest?.id;
 const steps=quest?.steps||[],lastStep=Math.max(0,steps.length-1),atSubmission=stepIndex>=lastStep;
 function focusAnswer(){
  answerPanel.current?.scrollIntoView({block:'nearest'});
  if(answerInput.current&&!answerInput.current.disabled)answerInput.current.focus({preventScroll:true});
  else answerPanel.current?.focus({preventScroll:true});
 }
 async function advanceStep(){
  if(!quest)return;
  if(stepIndex===0){
   await loadFile(quest.source_file);
   setTimeout(()=>{editor.current?.revealLineInCenter(quest.line);editor.current?.setPosition({lineNumber:quest.line,column:1})},80);
  }
  const next=Math.min(stepIndex+1,lastStep);setStepIndex(next);
  if(textQuestion&&next===lastStep)focusAnswer();
 }
 function jumpToSource(){
  if(!quest)return;
  void loadFile(quest.source_file);
  setTimeout(()=>{editor.current?.revealLineInCenter(quest.line);editor.current?.setPosition({lineNumber:quest.line,column:1})},100);
 }
 const runCurrentButton=<button className="button small" disabled={!source||!!busy} onClick={()=>void run(false)} title="仅运行当前文件，不验收关卡"><Play size={15}/>运行当前文件</button>;
 const stopButton=<button className="icon-button" aria-label="停止运行" disabled={busy!=='本机运行中'} onClick={()=>void api<{stopped:boolean}>(`/projects/${pid}/stop`,'POST',{}).then(r=>setMessage(r.stopped?'已请求停止':'没有正在运行的进程')).catch(e=>onError(e.message))}><Square size={16}/></button>;
 const feedback=evaluation&&<section className={evaluation.correct?'evaluation pass':'evaluation'} role="status">
  <h3>{evaluation.evaluation==='ai_review'?'AI 评审反馈':'答案反馈'}</h3><p>{evaluation.feedback}</p>
  {evaluation.checks?.map((c,i)=><div className="review-check" key={i}><strong>{c.passed?'✓':'○'} {c.criterion}</strong><span>{c.evidence}</span></div>)}
 </section>;
 const runConsole=<section className="run-console">
  <header><span><Terminal size={15}/>本机运行记录</span><div>
   {textQuestion&&<>{runCurrentButton}{stopButton}</>}
   <button disabled={!!busy} onClick={()=>void repair()}><Sparkles size={14}/>AI 环境修复</button>
   <button disabled={!!busy} onClick={()=>void repair(true)}><Undo2 size={14}/>撤销修复</button>
  </div></header>
  {busy&&<p className="busy-message"><i/>{busy}…{busy==='本机运行中'?' 最长 30 秒，可停止进程。':''}</p>}
  {message&&<p className="workspace-message" role="status">{message}</p>}
  {runs[0]?<>
   <div className={'run-result '+runs[0].status}><strong>{runStatus[runs[0].status]||runs[0].status}</strong><span>退出码 {runs[0].exit_code??'—'} · {runs[0].duration_ms} ms</span></div>
   <pre className="command">$ {runs[0].command.join(' ')}</pre><pre>{runs[0].stdout||'（无标准输出）'}</pre>
   {runs[0].stderr&&<pre className="stderr">{runs[0].stderr}</pre>}
   {runs.length>1&&<details><summary>以前的 {runs.length-1} 次运行</summary>{runs.slice(1).map((r,i)=><div key={i}><p>{runStatus[r.status]} · {r.duration_ms} ms</p><pre>{r.stdout}{r.stderr}</pre></div>)}</details>}
  </>:<p className="subtle">尚无运行记录。编辑源码后运行，查看实际输出。</p>}
  {repairLog&&<details open><summary>AI 环境修复记录</summary><pre>{repairLog}</pre></details>}
  <p className="execution-note">本地执行不是隔离沙箱，仅运行可信源码。AI 环境修复不代替本关业务修改。</p>
 </section>;
 return <Modal title="源码工作台" onClose={onClose} wide locked={!!busy} className={`workspace-panel${textQuestion?' text-workbench':''}`}>
  <div className="workspace-layout">
   <aside className="task-guide">
    <div className="guide-scroll">
     {quest?<>
      <div className="task-guide-header">
       <span className="tag">第 {(quest.order??0)+1} 关 · {quest.status==='completed'?'已完成 / 回顾':mode==='answer'?'源码问答题':mode==='explain'?'源码解释题':mode==='review'?'运行 + AI 差异评审':'真实运行验收'}</span>
       <h2>{quest.title}</h2><p>{quest.objective}</p>
      </div>
      <div className="current-step-label">{textQuestion?'阅读指引':'当前步骤'} {Math.min(stepIndex+1,steps.length)} / {steps.length}</div>
      {textQuestion&&<p className="reading-note">指引帮助你阅读源码，不代表通关。可以随时去右侧作答，提交后按本关标准验收。</p>}
      <ol className="task-steps">{steps.map((step,i)=><li key={i} className={i===stepIndex?'active-step':i<stepIndex?'read-step':'future-step'} aria-current={i===stepIndex?'step':undefined}>
       <span>{i+1}</span><div><h3>{step.title}</h3><p>{step.instruction}</p></div>
      </li>)}</ol>
      <button className="source-jump" onClick={jumpToSource}><Code2 size={15}/><span>{quest.source_file}:{quest.line}</span><ArrowUpRight size={15}/></button>
      <div className="criteria"><h3>本关验收标准</h3><p>{quest.verification}</p></div>
      {quest.status==='completed'&&<p className="inline-success"><Check size={17}/>本关已通过，可查看源码和{ textQuestion?'作答区':'运行记录'}。</p>}
      {!textQuestion&&feedback}
      <div className="hint-section">
       <button className="text-button" disabled={!!busy||!canGrade} onClick={()=>void act('读取提示中',async()=>{const r=await api<{hint:string;used:number}>(`/projects/${pid}/quests/${quest.id}/hint`,'POST',{});setHints(h=>[...h,r.hint]);setUsed(r.used)})}><Lightbulb size={16}/>获取分级线索 <small>已用 {used}</small></button>
       {hints.map((h,i)=><p className="hint" key={i}>{h}</p>)}
      </div>
      {quest.concepts?.length>0&&<div className="concepts"><span>相关资料</span>{quest.concepts.map(c=><button key={c} onClick={()=>onLibrary(c)}><BookOpen size={12}/>{c}</button>)}</div>}
     </>:<p className="subtle">选择章节中的关卡可查看对应操作步骤。</p>}
    </div>
    {quest&&<footer className="guide-footer">
     {textQuestion?<>
      <button className="button primary guide-primary" onClick={focusAnswer}>{canGrade?'去作答':'查看作答区'}<ArrowUpRight size={16}/></button>
      {!atSubmission&&canGrade&&<button className="button guide-next" disabled={!!busy} onClick={()=>void advanceStep()}>下一步阅读指引<ArrowUpRight size={15}/></button>}
      <small>提交答案并通过验收，才能解锁下一关。</small>
     </>:canGrade?<>
      {!atSubmission?<button className="button primary guide-primary" disabled={!!busy} onClick={()=>void advanceStep()}>{stepIndex===0?'定位源码，开始操作':'操作完成，进入下一步'}<ArrowUpRight size={16}/></button>:
       mode==='run'?<button className="button primary guide-primary" disabled={!source||!!busy} onClick={()=>void run(true)}><Play size={16}/>{busy||'保存并运行本关'}</button>:
       mode==='review'?<><p>先修改引用源码并实际运行，运行通过后提交差异。</p><button className="button primary guide-primary" disabled={!!busy||!source} onClick={()=>void (reviewReady?submit():run(true))}>{reviewReady?<Sparkles size={16}/>:<Play size={16}/>} {busy||(reviewReady?'提交 AI 差异评审':'保存并运行本关')}</button></>:null}
     </>:<small>本关已完成，可回顾源码与运行记录。</small>}
    </footer>}
   </aside>
   <div className="code-area">
    <header className="code-toolbar">
     <select aria-label="当前源码文件" value={source?.path||''} disabled={!!busy} onChange={e=>void loadFile(e.target.value)}><option value="" disabled>选择文件</option>{files.map(f=><option value={f.path} key={f.path}>{f.path}</option>)}</select>
     <div>
      {textQuestion&&<button className="button primary small" onClick={focusAnswer}>去作答<ArrowUpRight size={15}/></button>}
      <button className={diff?'button small selected':'button small'} disabled={!source} onClick={()=>setDiff(!diff)}><GitCompareArrows size={15}/>差异</button>
      <button className="button small" disabled={!dirty||!!busy} onClick={()=>void act('保存中',save)}><Save size={15}/>保存{dirty?' *':''}</button>
      {!textQuestion&&<>{runCurrentButton}{stopButton}</>}
     </div>
    </header>
    {!sameSource&&quest&&<p className="scope-note">{textQuestion?'正在查看其他项目文件。可返回左侧引用源码阅读，在作答区填写本关答案。':'正在查看其他项目文件。左侧主操作按本关入口验收；工具栏可单独运行当前文件。'}</p>}
    {conflict&&<div className="conflict-note">当前副本在草稿建立后已变化，草稿已保留。保存会检查版本冲突。<button onClick={()=>{if(source)void loadFile(source.path,true)}}>重新读取副本（保留草稿备份）</button></div>}
    <div className="editor-caption"><span>{diff?'导入原始基线 ↔ 当前草稿':'练习副本 · UTF-8'}{dirty?' · 未保存草稿':''}</span><span>{source?.language}</span></div>
    <div className="monaco-wrap">{source?(diff?
     <SourceDiff projectId={pid} path={source.path} language={source.language} original={source.original} modified={content}/>:
     <Editor height="100%" theme="vs-dark" path={pid+'/'+source.path} language={source.language} value={content} onChange={edit} line={sameSource?quest?.line:1} onMount={ed=>{editor.current=ed}} options={{readOnly:!!busy,occurrencesHighlight:'off',fontSize:15,fontFamily:'Cascadia Code, Consolas, monospace',minimap:{enabled:false},padding:{top:18},scrollBeyondLastLine:false,automaticLayout:true,tabSize:4}}/>
    ):<div className="empty-state">正在读取源码…</div>}</div>
    {source&&<div className="symbols">{files.find(f=>f.path===source.path)?.symbols.slice(0,7).map((s,i)=><button key={i} onClick={()=>{editor.current?.revealLineInCenter(s.line);editor.current?.setPosition({lineNumber:s.line,column:1})}}>ƒ {s.name} <small>{s.line}</small></button>)}</div>}
    {textQuestion&&quest&&<section className="answer-panel answer-area" aria-label="本关作答" ref={answerPanel} tabIndex={-1}>
     <header><div><label htmlFor="quest-answer">{quest.status==='completed'?'作答回顾':mode==='explain'?'结合源码解释你的判断':'写下你的答案'}</label><p id="answer-instructions">{quest.status==='completed'?'本关已通过，可回顾源码与验收标准。':mode==='explain'?'提交后由 AI 按本关标准逐项评审，全部通过后解锁下一关。':'提交后由本地服务校验答案，通过后解锁下一关。'}</p></div>
      {quest.status!=='completed'&&<button className="button primary" disabled={!answer.trim()||!!busy||!canGrade} onClick={()=>void submit()}><Check size={16}/>{busy==='AI 评审中'||busy==='校验答案中'?busy:'提交答案'}</button>}
     </header>
     <textarea id="quest-answer" ref={answerInput} aria-describedby="answer-instructions" disabled={!!busy||!canGrade} value={answer} onChange={e=>{setAnswer(e.target.value);setEvaluation(null)}} placeholder={quest.status==='completed'?'本关已完成，可以回顾源码与验收标准。':mode==='explain'?'在这里填写解释：引用相关函数、变量或调用关系，说明判断依据…':'在这里填写你的观察或预测…'}/>
     {quest.status==='completed'&&<p className="inline-success"><Check size={16}/>本关已通过，回顾时不能重复提交。</p>}
     {busy&&<p className="busy-message" role="status"><i/>{busy}…</p>}
     {message&&<p className="workspace-message answer-message" role="status">{message}</p>}
     {feedback}
    </section>}
    {textQuestion?<details className="optional-run-tools"><summary>运行工具（可选，与本关作答无关）</summary>{runConsole}</details>:runConsole}
   </div>
  </div>
 </Modal>
}
