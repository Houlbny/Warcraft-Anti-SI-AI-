import {useEffect,useRef,useState} from 'react';
import {FolderOpen,BookOpen,Settings2,Map,ChevronRight,Lock,Check,Flame,ScrollText,ArrowRight,RefreshCw,X,ShieldCheck} from 'lucide-react';
import {api,roles,type Campaign,type Project,type Quest,type Role,type Settings} from './api';
import World from './World';
import Character from './Character';
import Workbench from './Workbench';
import GenerationScene from './GenerationScene';
import StoryPanel from './StoryPanel';
import {prologue,questStory,questCompletionStory,roleStories,storyBrand} from './story';
import './story.css';
import {Modal,ProjectsPanel,SettingsPanel,LibraryPanel} from './Panels';
type Panel='projects'|'settings'|'library'|'story'|null;
export default function App(){
 const[projects,setProjects]=useState<Project[]>([]),[pid,setPid]=useState(localStorage.getItem('codewarden.project')||''),[campaign,setCampaign]=useState<Campaign|null>(null),[selectedQid,setSelectedQid]=useState(''),[selectedRole,setSelectedRole]=useState<Role|null>(null),[panel,setPanel]=useState<Panel>(null),[workspace,setWorkspace]=useState(false),[routeOpen,setRouteOpen]=useState(false),[loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[recovering,setRecovering]=useState(false),[now,setNow]=useState(Date.now()),[error,setError]=useState(''),[notice,setNotice]=useState(''),[returnToRole,setReturnToRole]=useState(false),[libraryQuery,setLibraryQuery]=useState(''),[result,setResult]=useState<{quest:Quest;feedback:string}|null>(null);
 const generationAttempt=useRef(0);
 const deletedProjectIds=useRef(new Set<string>());
 const projectListRequest=useRef(0);
 const pendingGeneration=useRef<{attempt:number;baselineStartedAt:string|null;observedActive:boolean}|null>(null);
 const project=projects.find(p=>p.id===pid),quests=campaign?.quests||[],quest=quests.find(q=>q.id===selectedQid)||null,profile=roles.find(r=>r.id===(campaign?.role||selectedRole));
 const ready=campaign?.status==='ready'||campaign?.status==='completed',needsSetup=!!project&&!!campaign&&!ready;
 async function refreshProjects(){const request=++projectListRequest.current;const ps=(await api<Project[]>('/projects')).filter(p=>!deletedProjectIds.current.has(p.id));if(request===projectListRequest.current)setProjects(ps);return ps}
 useEffect(()=>{refreshProjects().then(ps=>{const chosen=ps.find(p=>p.id===pid)||ps[0];if(chosen)setPid(chosen.id);else{setPid('');setLoading(false)}}).catch(e=>{setError(e.message);setLoading(false)})},[]);
 useEffect(()=>{if(!pid){setCampaign(null);return}let active=true;generationAttempt.current++;pendingGeneration.current=null;setRecovering(false);setBusy(false);setLoading(true);setCampaign(null);setSelectedRole(null);setSelectedQid('');setWorkspace(false);setResult(null);setRouteOpen(false);localStorage.setItem('codewarden.project',pid);api<Campaign>(`/projects/${pid}/campaign`).then(c=>{if(!active||deletedProjectIds.current.has(pid))return;setCampaign(c);setSelectedRole(c.role);setSelectedQid(c.current_quest_id||c.quests[c.quests.length-1]?.id||'')}).catch(e=>{if(active&&!deletedProjectIds.current.has(pid))setError(e.message)}).finally(()=>{if(active&&!deletedProjectIds.current.has(pid))setLoading(false)});return()=>{active=false}},[pid]);
 function observeGeneration(c:Campaign){
  setCampaign(c);setRecovering(false);
  if(c.status==='ready'||c.status==='completed'){
   generationAttempt.current++;setBusy(false);setError('');setNotice('');
   setSelectedQid(c.current_quest_id||c.quests[0]?.id||'');setResult(null);
  }else if(c.status==='failed'){
   generationAttempt.current++;setBusy(false);setError(c.error||'章节生成失败，请检查设置后重试。');
  }
 }
 useEffect(()=>{
  if(!pid||(campaign?.status!=='generating'&&!recovering))return;
  let active=true,inFlight=false;
  const timer=setInterval(()=>{if(inFlight)return;inFlight=true;void api<Campaign>(`/projects/${pid}/campaign`).then(c=>{
   if(!active||deletedProjectIds.current.has(pid))return;
   // A POST may still be reaching the handler; an old unprepared state is not its outcome.
   if(c.status==='unprepared'&&busy)return;
   const pending=pendingGeneration.current;
   // Before the POST reaches the handler, polling can still see the previous failed attempt.
   if(pending&&pending.attempt===generationAttempt.current){
    if(c.status==='failed'&&!pending.observedActive&&(!c.started_at||c.started_at===pending.baselineStartedAt))return;
    if(c.status==='generating')pending.observedActive=true;
   }
   observeGeneration(c);
  }).catch(()=>{if(active&&!deletedProjectIds.current.has(pid))setNotice('暂时无法查询生成状态，正在继续确认结果。请勿重复提交生成。')}).finally(()=>{inFlight=false})},3000);
  return()=>{active=false;clearInterval(timer)};
 },[campaign?.status,recovering,busy,pid]);
 useEffect(()=>{
  if(campaign?.status!=='generating'&&!recovering&&!busy)return;
  setNow(Date.now());const timer=setInterval(()=>setNow(Date.now()),1000);
  return()=>clearInterval(timer);
 },[campaign?.status,recovering,busy]);
 async function generate(){
  if(!selectedRole||!project||busy||recovering||campaign?.status==='generating')return;
  const attempt=++generationAttempt.current;let posted=false;
  setError('');setNotice('');setBusy(true);
  try{
   const s=await api<Settings>('/settings');
   if(attempt!==generationAttempt.current)return;
   let localEndpoint=false;try{localEndpoint=['localhost','127.0.0.1','::1','[::1]'].includes(new URL(s.base_url).hostname.toLowerCase())}catch{}
   if(!s.base_url||!s.model||(!s.key_set&&!localEndpoint)){setReturnToRole(true);setPanel('settings');return}
   setCampaign(c=>c?{...c,status:'generating',role:selectedRole,generation:c.role===selectedRole?c.generation:undefined,error:undefined,started_at:undefined,phase:undefined,source_count:undefined,input_bytes:undefined,wait_timeout_seconds:undefined}:c);
   posted=true;pendingGeneration.current={attempt,baselineStartedAt:campaign?.started_at||null,observedActive:false};
   const c=await api<Campaign>(`/projects/${pid}/campaign`,'POST',{role:selectedRole});
   if(attempt!==generationAttempt.current)return;
   pendingGeneration.current=null;observeGeneration(c);await refreshProjects();
  }catch(e){
   if(attempt!==generationAttempt.current)return;
   const message=(e as Error).message;
   if(!posted){setError(message);return}
   pendingGeneration.current=null;
   // Losing the POST response does not establish that the paid model request failed.
   try{
    const c=await api<Campaign>(`/projects/${pid}/campaign`);
    if(attempt!==generationAttempt.current)return;
    observeGeneration(c);
    if(c.status==='generating')setNotice('连接中断，但后台仍在生成。正在查询结果，请勿重复提交。');
    else if(c.status==='failed')setError(c.error||message);
    else if(c.status==='unprepared')setError('服务端尚未开始生成，请检查连接后再提交。');
   }catch{
    if(attempt!==generationAttempt.current)return;
    setRecovering(true);setNotice('连接中断，生成结果尚未确认。正在查询后台状态，请勿重复提交。');
   }
  }finally{if(pendingGeneration.current?.attempt===attempt)pendingGeneration.current=null;if(attempt===generationAttempt.current)setBusy(false)}
 }

 function deletedProject(id:string,message:string){
  deletedProjectIds.current.add(id);projectListRequest.current++;
  setProjects(ps=>ps.filter(p=>p.id!==id));setError('');setNotice(message);
  if(pid!==id)return;
  // Invalidate pending generation and campaign responses before React runs effect cleanup.
  generationAttempt.current++;pendingGeneration.current=null;
  setPid('');setCampaign(null);setSelectedQid('');setSelectedRole(null);
  setWorkspace(false);setResult(null);setRouteOpen(false);setReturnToRole(false);
  setBusy(false);setRecovering(false);setLoading(false);setPanel('projects');
  try{localStorage.removeItem('codewarden.project')}catch{}
 }
 function selectProject(p:Project){if(deletedProjectIds.current.has(p.id))return;if(p.id===pid){setPanel(null);return}setPid(p.id);setPanel(null);setReturnToRole(false)}
 function selectQuest(index:number){const q=quests[index];if(!q||q.status==='locked'||busy)return;setSelectedQid(q.id);setResult(null);setRouteOpen(false)}
 async function passed(q:Quest,feedback:string){if(deletedProjectIds.current.has(pid))return;const c=await api<Campaign>(`/projects/${pid}/campaign`);if(deletedProjectIds.current.has(pid))return;setCampaign(c);await refreshProjects();if(deletedProjectIds.current.has(pid))return;setResult({quest:q,feedback});setWorkspace(false)}
 function nextQuest(){setResult(null);setWorkspace(false);if(campaign?.current_quest_id){setSelectedQid(campaign.current_quest_id);setRouteOpen(false)}else setRouteOpen(true)}
 function openLibrary(query=''){setLibraryQuery(query);setPanel('library')}
 const generating=!ready&&(busy||recovering||campaign?.status==='generating');
 const showGenerationScene=generating||(campaign?.status==='failed'&&!!campaign.generation);
 const canResume=campaign?.status==='failed'&&campaign.generation?.resumable&&selectedRole===campaign.role;
 const changedRole=campaign?.status==='failed'&&!!selectedRole&&!!campaign.role&&selectedRole!==campaign.role;
 const started=campaign?.started_at?Date.parse(campaign.started_at):NaN;
 const elapsed=Number.isFinite(started)?Math.max(0,Math.floor((now-started)/1000)):null;
 const storyRole=campaign?.role||selectedRole||'mage';
 const currentStory=quest?(quest.status==='completed'?questCompletionStory(quest,storyRole,campaign?.status==='completed'&&quest.order===(campaign?.total||quests.length)-1,campaign?.total||quests.length):questStory(quest,storyRole,campaign?.total||quests.length)):null;
 const completionStory=result?questCompletionStory(result.quest,storyRole,campaign?.status==='completed',campaign?.total||quests.length):null;
 return <main className={`game-shell role-${campaign?.role||selectedRole||'mage'}`}><div className="stage-backdrop"><World quests={ready?quests:[]} completed={campaign?.completed||0} onSelect={selectQuest} role={campaign?.role||selectedRole||undefined} currentQuestId={selectedQid}/></div><div className="stage-shade"/>
 <header className="game-hud"><button className="project-hud" onClick={()=>setPanel('projects')} disabled={generating}><span className="camp-seal"><Flame size={22}/></span><span><small>{storyBrand.title}<span className="hud-camp"> · {storyBrand.camp}</span></small><strong>{project?.name||'尚未载入项目'}</strong></span><ChevronRight size={17}/></button><div className="chapter-hud">{ready?<><span>{profile?.name} · {campaign?.title}</span><div className="hud-progress"><i style={{width:`${campaign?.total?(campaign.completed/campaign.total)*100:0}%`}}/></div><small>{campaign?.completed} / {campaign?.total} 关完成</small></>:<span>{loading?'正在读取档案…':project?'章节尚未准备':'导入项目后选择职业'}</span>}</div><nav className="hud-tools"><button aria-label="故事" title="故事" onClick={()=>setPanel('story')}><ScrollText size={19}/><span>故事</span></button><button aria-label="章节路线" title="章节路线" className={routeOpen?'selected':''} disabled={!ready} onClick={()=>setRouteOpen(!routeOpen)}><Map size={19}/><span>路线</span></button><button aria-label="练习档案" title="练习档案" onClick={()=>setPanel('projects')} disabled={generating}><FolderOpen size={19}/><span>档案</span></button><button aria-label="知识档案" title="知识档案" onClick={()=>openLibrary()}><BookOpen size={19}/><span>资料</span></button><button aria-label="模型设置" title="模型设置" onClick={()=>{setReturnToRole(needsSetup);setPanel('settings')}} disabled={generating}><Settings2 size={19}/><span>设置</span></button></nav></header>
 {(error||notice)&&<div className={error?'stage-toast error':'stage-toast'} role={error?'alert':'status'}><span>{error||notice}</span><button aria-label="关闭通知" onClick={()=>{setError('');setNotice('')}}><X size={17}/></button></div>}
 {ready&&!routeOpen&&quest&&<button className="current-quest-marker game-panel" onClick={()=>setRouteOpen(true)}><ScrollText size={18}/><span><small>当前查看 · 第 {(quest.order??0)+1} 关</small><strong>{quest.title}</strong></span><Map size={15}/></button>}
 {ready&&routeOpen&&<aside className="route-panel game-panel" aria-label="章节路线列表"><header><div><small>{profile?.name}路线</small><h2>{campaign?.title}</h2></div><button className="icon-button" aria-label="收起章节路线" onClick={()=>setRouteOpen(false)}><X size={17}/></button></header><p>{campaign?.summary}</p><ol>{quests.map((q,i)=><li key={q.id}><button disabled={q.status==='locked'} className={selectedQid===q.id?'route-quest current':'route-quest'} onClick={()=>selectQuest(i)}><span className={`route-number ${q.status}`}>{q.status==='completed'?<Check size={16}/>:q.status==='locked'?<Lock size={14}/>:i+1}</span><span><strong>{q.title}</strong><small>{q.status==='locked'?'完成上一关后解锁':q.status==='completed'?'已完成 · 可回顾':'可挑战'}</small></span>{selectedQid===q.id&&<ChevronRight size={15}/>}</button></li>)}</ol>{campaign?.coverage&&<p className="coverage-note">{campaign.coverage}</p>}</aside>}
 {ready&&quest&&!workspace&&<section className="quest-dialogue story-quest-dialogue game-panel" aria-label="当前关卡指引"><div className="dialogue-portrait"><Character role={campaign!.role||'mage'} size={90}/><span>{profile?.name}</span></div><div className="dialogue-content"><div className="dialogue-label"><span>第 {(quest.order??0)+1} / {campaign?.total} 关</span><span>{quest.status==='completed'?'已完成 · 回顾':quest.verification_mode==='explain'?'源码解释题':quest.verification_mode==='review'?'实际运行 + AI 差异评审':quest.verification_mode==='run'?'本机真实运行验收':'源码问答题'}</span><span className="ai-tag">AI 生成章节</span></div><h1>{quest.title}</h1><p>{quest.brief}</p><div className="dialogue-objective"><strong>目标</strong><span>{quest.objective}</span></div>{currentStory&&<aside className="quest-story" aria-label="本关战地手记"><small>{currentStory.eyebrow} · 游戏剧情</small><strong>{currentStory.speaker} · {currentStory.title}</strong><p>{currentStory.body}</p></aside>}</div><div className="dialogue-actions">{quest.status==='completed'&&campaign?.current_quest_id?<><button className="button primary" onClick={nextQuest}>进入当前关卡<ArrowRight size={18}/></button><button className="text-button" onClick={()=>setWorkspace(true)}>回看本关源码</button></>:<><button className="button primary" onClick={()=>setWorkspace(true)}>{quest.status==='completed'?'回看本关源码':quest.verification_mode==='answer'?'查看源码并作答':quest.verification_mode==='explain'?'查看源码并解释':'打开工作台开始修改'}<ChevronRight size={18}/></button><span>{quest.steps?.length||0} 个操作步骤 · 引用 {quest.source_file}:{quest.line}</span></>}</div></section>}
 {!project&&!loading&&!panel&&<section className="start-dialogue story-start game-panel" aria-label="灰烬营地序章"><span className="story-label">{storyBrand.camp} · 游戏剧情</span><h1>{prologue.title}</h1><p>{prologue.paragraphs[0]}</p><p className="story-start-task">导入项目或示例源码，选择法师、圣骑士或德鲁伊，再由 AI 预生成所选路线，按顺序完成关卡。</p><div className="story-start-actions"><button className="button primary" onClick={()=>setPanel('projects')}>导入项目<FolderOpen size={17}/></button><button className="text-button" onClick={()=>setPanel('story')}>查看序章</button></div></section>}
 {project&&!campaign&&!loading&&!panel&&<section className="start-dialogue game-panel"><h2>未能读取章节</h2><p>检查本地服务后重试，已有源码和草稿仍保留。</p><button className="button primary" onClick={()=>{setLoading(true);void api<Campaign>(`/projects/${pid}/campaign`).then(c=>{if(deletedProjectIds.current.has(pid))return;setCampaign(c);setSelectedRole(c.role);setSelectedQid(c.current_quest_id||c.quests[0]?.id||'')}).catch(e=>{if(!deletedProjectIds.current.has(pid))setError(e.message)}).finally(()=>{if(!deletedProjectIds.current.has(pid))setLoading(false)})}}><RefreshCw size={16}/>重新读取</button></section>}
 {loading&&<div className="stage-loading"><span className="spinner"/>正在读取练习档案…</div>}
 {ready&&!workspace&&!panel&&!result&&<div className="world-controls"><span>方向键 / WASD 移动</span><span>点击已解锁节点查看关卡</span></div>}
 {needsSetup&&!panel&&<Modal title={generating?'正在准备完整章节':showGenerationScene?'章节生成已暂停':'选择本次探索的职业'} onClose={()=>setPanel('projects')} wide locked={generating} className={showGenerationScene?"role-panel gen-active-panel":"role-panel"}><div className="setup-project"><FolderOpen size={17}/><strong>{project?.name}</strong><span>{project?.languages.join(' / ')}</span></div>{showGenerationScene?<>{!generating&&<section className="gen-failure-controls"><div className="generation-error" role="status"><h3>生成已停止 · 已保存 {campaign?.generation?.quests_completed||0} / {campaign?.generation?.quests_total||0} 关</h3><p>{campaign?.error||'未能完成本轮生成，草稿进度已保留。'}</p><button className="text-button" onClick={()=>{setReturnToRole(true);setPanel('settings')}}>检查模型设置<Settings2 size={15}/></button></div><div className="gen-resume-row"><label>本次生成职业<select aria-label="本次生成职业" value={selectedRole||''} onChange={e=>setSelectedRole(e.target.value as Role)}>{roles.map(r=><option key={r.id} value={r.id}>{r.name} · {r.focus}</option>)}</select></label><button className="button primary" disabled={!selectedRole} onClick={()=>void generate()}>{canResume?'继续未完成部分':changedRole?'生成新职业路线':'重新生成所选路线'}<ArrowRight size={18}/></button></div><p className="gen-resume-copy">{changedRole?'已更换职业，将创建新的路线草稿；不会把此前职业的已存关卡当作本次进度。':canResume?'继续时从尚未完成的阶段接上；已保存的兼容结果由服务端保留。整章检查完成后才能开始闯关。':'此草稿当前无法续接。请检查设置后明确重新生成，完整章节通过检查后开放。'}</p></section>}<GenerationScene campaign={campaign!} role={campaign?.role||selectedRole||'mage'} active={generating} recovering={recovering} elapsed={elapsed}/></>:<><p className="panel-intro">角色会改变章节内容和提示深度。每个练习档案保留一条路线，生成后从第一关顺序推进。</p><div className="role-options">{roles.map(r=><button key={r.id} aria-pressed={selectedRole===r.id} className={selectedRole===r.id?'role-choice selected':'role-choice'} onClick={()=>setSelectedRole(r.id)}><span className="role-portrait"><Character role={r.id} size={100}/></span><h2>{r.name}</h2><strong>{r.focus}</strong><p>{r.description}</p><div className="role-introduction"><small>{roleStories[r.id].mentor} · {roleStories[r.id].epithet}</small><p>{roleStories[r.id].introduction}</p></div><ul>{r.details.map(d=><li key={d}>{d}</li>)}</ul><span className="choice-check">{selectedRole===r.id?<><Check size={16}/>已选择</>:'选择此职业'}</span></button>)}</div>{campaign?.status==='failed'&&<div className="generation-error" role="status"><h3>章节尚未生成</h3><p>{campaign.error||'生成失败，请检查设置后重试。'}</p><button className="text-button" onClick={()=>{setReturnToRole(true);setPanel('settings')}}>检查模型设置<Settings2 size={15}/></button></div>}<div className="setup-footer"><p><ShieldCheck size={15}/>按所选职业生成约 6–10 关，生成后开始。失败可重试。</p><button className="button primary" disabled={!selectedRole} onClick={()=>void generate()}>{campaign?.status==='failed'?'重新生成所选路线':'生成所选完整路线'}<ArrowRight size={18}/></button></div></>}</Modal>}
 {project&&ready&&<Workbench open={workspace&&!panel&&!result} project={project} quest={quest} onClose={()=>setWorkspace(false)} onPassed={passed} onError={setError} onLibrary={openLibrary}/>}
 {result&&<Modal title={campaign?.status==='completed'?'章节完成':'本关通过'} onClose={()=>setResult(null)} className="result-panel"><div className="result-emblem"><Check size={37}/></div><span className="tag">第 {(result.quest.order??0)+1} 关 · 验收通过</span><h2>{result.quest.title}</h2><p>{result.feedback}</p>{result.quest.success_message&&result.quest.success_message!==result.feedback&&<p className="success-story">{result.quest.success_message}</p>}{completionStory&&<section className="result-story" aria-label="通关战地手记"><small>{completionStory.eyebrow} · 游戏剧情</small><h3>{completionStory.title}</h3><strong>{completionStory.speaker}</strong><p>{completionStory.body}</p></section>}<div className="result-progress"><span>{campaign?.completed} / {campaign?.total} 关完成</span><strong>{campaign?.status==='completed'?'本章任务均已通过':`下一关：${quests.find(q=>q.id===campaign?.current_quest_id)?.title||'已解锁'}`}</strong></div><button className="button primary" onClick={nextQuest}>{campaign?.current_quest_id?'下一关':'查看章节记录'}<ArrowRight size={18}/></button><button className="text-button" onClick={()=>{setResult(null);setWorkspace(true)}}>回看源码与运行结果</button>{campaign?.coverage&&<p className="subtle">{campaign.coverage}</p>}</Modal>}
 {panel==='story'&&<StoryPanel onClose={()=>setPanel(null)} onImport={!project?()=>setPanel('projects'):undefined}/> }
 {panel==='projects'&&<ProjectsPanel projects={projects} activeId={pid} onSelect={selectProject} onChanged={refreshProjects} onDeleted={deletedProject} onError={setError} onClose={()=>setPanel(null)}/>}
 {panel==='settings'&&<SettingsPanel onClose={()=>setPanel(null)} onError={setError} onNotice={setNotice} onReturn={returnToRole?()=>{setPanel(null);setReturnToRole(false)}:undefined}/>}
 {panel==='library'&&<LibraryPanel initialQuery={libraryQuery} onClose={()=>setPanel(null)} onError={setError}/>}
 </main>
}
