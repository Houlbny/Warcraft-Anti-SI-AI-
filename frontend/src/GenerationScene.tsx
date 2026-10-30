import {useEffect,useState,type CSSProperties} from 'react';
import {BookOpen,ChevronLeft,ChevronRight,Pause,Play,Check,FileText} from 'lucide-react';
import Character from './Character';
import {type Campaign,type Role} from './api';
import './generation-scene.css';
import {generationStory,storyBrand} from './story';

const stages=[{id:'analyzing',name:'阅读源码',action:'分组分析项目源码',x:106},{id:'planning',name:'规划路线',action:'规划职业路线',x:344},{id:'quests',name:'整理关卡',action:'细化关卡与验收',x:582},{id:'checking',name:'整章检查',action:'检查章节一致性',x:820}] as const;
const tips=[
 '看源码时，先找入口和调用者，再追踪数据从哪里来、到哪里去。',
 '预测输出前，写下每一步变量的值；运行后对照差异定位理解上的偏差。',
 '一次只改一个明确问题，保留运行输出，方便比较改动前后的行为。',
 '退出码为零说明程序正常结束；是否符合目标，还要核对任务的验收标准。',
 '差异视图的一侧是导入基线，另一侧是当前草稿。关闭视图不会丢失草稿。',
 '出现错误时，先阅读第一条具体报错，并检查对应的文件、行号和输入条件。'
];

function CampArt({stage,role}:{stage:string|undefined;role:Role}){
 const current=stages.find(s=>s.id===stage);
 return <svg className="gen-camp-art" viewBox="0 0 1000 286" role="img" aria-label={current?`角色正在${current.name}，装饰动画不表示任务完成`:'角色在营地等待服务端生成状态'} shapeRendering="crispEdges">
  <defs><pattern id="gen-floor" width="64" height="24" patternUnits="userSpaceOnUse"><rect width="64" height="24" fill="#263b3a"/><path d="M0 23H64M32 0V23" stroke="#314945" strokeWidth="2"/></pattern><pattern id="gen-stars" width="174" height="86" patternUnits="userSpaceOnUse"><rect x="12" y="14" width="2" height="2" fill="#a2ad9b"/><rect x="122" y="45" width="3" height="3" fill="#8a9e8b"/></pattern></defs>
  <rect width="1000" height="286" fill="#10232b"/><rect width="1000" height="185" fill="url(#gen-stars)"/>
  <path d="M0 170V140H20V118H32V92H40V72H48V92H56V118H68V140H88V169H128V136H140V108H150V84H160V108H174V138H186V174H232V150H246V121H255V99H264V121H280V148H292V174H1000V190H0Z" fill="#183630"/>
  <path d="M1000 170V140H980V118H968V92H960V72H952V92H944V118H932V140H912V169H872V136H860V108H850V84H840V108H826V138H814V174H768V150H754V121H745V99H736V121H720V148H708V174H0V190H1000Z" fill="#1a3834"/>
  <rect y="204" width="1000" height="82" fill="url(#gen-floor)"/><rect y="201" width="1000" height="5" fill="#59634f"/><rect x="27" y="35" width="946" height="8" fill="#4e594a"/><rect x="32" y="35" width="9" height="171" fill="#3e5448"/><rect x="959" y="35" width="9" height="171" fill="#3e5448"/>
  {[74,294,532,770,934].map((x,i)=><g key={x}><path d={`M${x} 43v34h13v13`} fill="none" stroke="#758064" strokeWidth="3"/><rect x={x+7} y="86" width="15" height="24" fill="#a88c51"/><rect x={x+10} y="91" width="9" height="14" fill="#ead39a"/><rect className="gen-lantern-light" x={x-2} y="79" width="33" height="37" fill="#f0cf79" opacity={i%2?.08:.12}/></g>)}
  {/* Reading station: shelves and turning pages. */}
  <g className={stage==='analyzing'?'gen-station is-working':'gen-station'}><rect x="67" y="111" width="134" height="86" fill="#5a624f"/><rect x="74" y="118" width="120" height="70" fill="#192f31"/><path d="M74 149H194M74 176H194" stroke="#6c7053" strokeWidth="5"/>{Array.from({length:8},(_,i)=><rect key={i} x={81+i*13} y={123+(i%3)*2} width="8" height={23-(i%3)*2} fill={['#7d8d72','#bcab7b','#657c84'][i%3]}/>)}<rect x="78" y="156" width="48" height="17" fill="#7f8c6c"/><rect x="131" y="155" width="50" height="18" fill="#b3a173"/>
   <rect x="109" y="188" width="65" height="9" fill="#99835b"/><rect x="117" y="197" width="7" height="21" fill="#74684e"/><rect x="159" y="197" width="7" height="21" fill="#74684e"/>
   <g className="gen-book-pages"><path d="M115 177L140 181L166 176V189L140 194L115 189Z" fill="#dcd1a9"/><path d="M140 181V193M121 182L134 184M148 182L159 180M121 186L134 188M148 187L159 185" stroke="#8c906d" strokeWidth="2"/><path className="gen-turn-page" d="M140 181L164 177V188L140 194Z" fill="#f0e7cb"/></g>
  </g>
  {/* Route station: paper map, ink dots and a moving stylus. */}
  <g className={stage==='planning'?'gen-station is-working':'gen-station'}><rect x="302" y="169" width="140" height="14" fill="#8a7958"/><rect x="312" y="183" width="9" height="36" fill="#5b5946"/><rect x="422" y="183" width="9" height="36" fill="#5b5946"/><path d="M310 139L418 131L431 167L320 174Z" fill="#d1c495"/><path d="M323 151L349 145L371 157L399 143L416 151" fill="none" stroke="#637d69" strokeWidth="4" strokeDasharray="5 4"/>{[323,349,371,399,416].map((x,i)=><rect key={x} x={x} y={[149,143,155,141,149][i]} width="5" height="5" fill="#7f7551"/>)}<rect x="431" y="151" width="10" height="16" fill="#213b46"/><g className="gen-map-pencil"><path d="M365 151L378 119L383 121L371 153Z" fill="#e6c989"/><rect x="375" y="117" width="7" height="6" fill="#bda76d"/></g><path d="M321 138l-4 8M418 133l5 8" stroke="#ede0b5" strokeWidth="5"/></g>
  {/* Quest station: validated drafts are put away one at a time. */}
  <g className={stage==='quests'?'gen-station is-working':'gen-station'}><rect x="548" y="162" width="140" height="18" fill="#8b7b5c"/><rect x="559" y="180" width="9" height="41" fill="#69634e"/><rect x="667" y="180" width="9" height="41" fill="#69634e"/><rect x="638" y="119" width="34" height="38" fill="#b1a77b"/><path d="M644 127H665M644 134H661M644 141H663" stroke="#6d8067" strokeWidth="3"/><path d="M561 152H599V161H561ZM566 145H604V154H566ZM571 138H609V147H571Z" fill="#dcd0a3"/><g className="gen-sort-paper"><rect x="604" y="122" width="28" height="35" fill="#e6d9b0"/><path d="M610 130H625M610 137H625M610 144H619" stroke="#819376" strokeWidth="2"/></g><rect x="552" y="152" width="9" height="11" fill="#475e55"/></g>
  {/* Check station: an animated lens scans, without stamping a premature success. */}
  <g className={stage==='checking'?'gen-station is-working':'gen-station'}><rect x="792" y="113" width="116" height="83" fill="#656e56"/><rect x="800" y="121" width="100" height="67" fill="#cecca9"/><path d="M815 132H885M815 145H880M815 158H887M815 171H875" stroke="#8c9773" strokeWidth="3"/><path d="M807 132h4M807 145h4M807 158h4M807 171h4" stroke="#536e57" strokeWidth="4"/><rect x="804" y="196" width="10" height="26" fill="#5b624b"/><rect x="885" y="196" width="10" height="26" fill="#5b624b"/><g className="gen-check-lens"><rect x="841" y="133" width="24" height="22" fill="#5d887e" fillOpacity=".6" stroke="#c7bb84" strokeWidth="4"/><path d="M862 154l14 17" stroke="#c7bb84" strokeWidth="6"/></g></g>
  <path d="M125 249H867" stroke="#8a8961" strokeWidth="3" strokeDasharray="5 9" opacity=".45"/>
  {stages.map(s=><g key={s.id}><rect x={s.x+15} y="246" width="28" height="7" fill={s.id===stage?'#dfc27e':'#526654'}/><text x={s.x+29} y="277" textAnchor="middle" fill={s.id===stage?'#e9d5a2':'#95aa8b'} fontSize="13" fontFamily="Microsoft YaHei, sans-serif">{s.name}</text></g>)}
  <g className="gen-avatar-position" style={{transform:`translate(${current?.x??455}px, 161px)`}}><ellipse cx="32" cy="80" rx="29" ry="6" fill="#071a20" opacity=".7"/><g className="gen-avatar-action"><Character role={role} size={82}/></g><g className="gen-work-spark"><rect x="72" y="26" width="4" height="4" fill="#e5cc8c"/><rect x="64" y="15" width="3" height="3" fill="#c3d8ac"/></g></g>
 </svg>
}

export default function GenerationScene({campaign,role,active,recovering=false,elapsed}:{campaign:Campaign;role:Role;active:boolean;recovering?:boolean;elapsed:number|null}){
 const[paused,setPaused]=useState(false),[reduced,setReduced]=useState(()=>window.matchMedia('(prefers-reduced-motion: reduce)').matches),[tip,setTip]=useState(0);
 useEffect(()=>{const media=window.matchMedia('(prefers-reduced-motion: reduce)'),update=()=>setReduced(media.matches);media.addEventListener('change',update);return()=>media.removeEventListener('change',update)},[]);
 const progress=campaign.generation,stage=progress?.stage,current=stages.find(s=>s.id===stage),stopped=!active||paused||reduced;
 const total=progress?.quests_total||progress?.quest_titles.length||0,saved=Math.max(0,Math.min(progress?.quests_completed||0,total));
 const phase=campaign.phase?{preparing:'整理请求材料',requesting:'等待本段模型响应',validating:'校验本段结果'}[campaign.phase]:'正在确认后台状态';
 const heading=!active?'草稿进度已保留':recovering?'正在确认后台生成状态':current?.action||'正在等待章节响应';
 return <section className="generation-scene" data-stage={stage||'legacy'} data-motion={stopped?'paused':'running'} aria-label="章节生成营地"><header className="gen-scene-header"><div><span>{storyBrand.camp} · 章节草稿工作台</span><h2>{heading}</h2></div><button className="gen-motion-control" aria-label={reduced?'系统已减少动态效果':paused?'播放等待动画':'暂停等待动画'} aria-pressed={stopped} disabled={reduced||!active} onClick={()=>setPaused(p=>!p)}>{stopped?<Play size={15}/>:<Pause size={15}/>}<span>{!active?'动画已停止':reduced?'已减少动态效果':paused?'播放动画':'暂停动画'}</span></button></header><div className="gen-stage-strip" aria-label="生成阶段">{stages.map(s=><span key={s.id} aria-current={stage===s.id?'step':undefined} className={stage===s.id?'gen-stage-active':''}>{s.name}</span>)}</div><div className="gen-camp-frame" style={{'--gen-camera-offset':`${((current?.x??455)+40)/1000*250}%`} as CSSProperties}><CampArt stage={stage} role={role}/></div><div className="gen-live-row" role="status"><span className="gen-phase-dot"/><strong>{!active?'本轮生成已停止，请查看失败原因与后续操作。':progress?.message||phase}</strong>{active&&elapsed!==null&&<span className="gen-elapsed">本轮已等待 {elapsed} 秒</span>}</div>
  <aside className="generation-story" aria-label="营地剧情"><small>营地手记 · 游戏剧情</small><p>{!active?'导师合上了行囊旁的手记，等你查明中断原因后再出发。':recovering?'传讯暂时中断，营地正在等待消息；路线是否准备妥当仍待确认。':paused?'你将营地图景停在了这一页。':generationStory(stage,role)}</p></aside>
  {progress?<div className="gen-real-counters"><span>分析已保存 <strong>{progress.analysis_completed} / {progress.analysis_total} 组</strong></span><span>关卡已保存 <strong>{saved} / {total} 关</strong></span>{progress.current_quest!==null&&<span>{active?'当前整理':'停止于'} <strong>第 {progress.current_quest} 关</strong></span>}<small>整章检查完成后开放闯关</small></div>:<p className="gen-legacy-note">此记录暂未提供分段进度。正在查询服务端状态，不展示估算完成度。</p>}
  {total>0&&<ol className="gen-quest-drafts" aria-label="已规划的关卡草稿">{Array.from({length:total},(_,i)=>{const reported=progress?.quest_titles[i]?.status||'pending';const status=reported==='generating'&&!active?'pending':reported;return <li key={i} data-status={status}><span className="gen-draft-icon">{status==='saved'?<Check size={15}/>:<FileText size={14}/>}</span><div><span>第 {i+1} 关 <small>{status==='saved'?'已保存':status==='generating'?'整理中':'待整理'}</small></span><strong>{progress?.quest_titles[i]?.title||'等待规划标题'}</strong></div></li>})}</ol>}
  <div className="gen-tips"><BookOpen size={17}/><div><span>本地小提示 · {tip+1} / {tips.length}</span><p>{tips[tip]}</p></div><button aria-label="上一条本地提示" onClick={()=>setTip(i=>(i+tips.length-1)%tips.length)}><ChevronLeft size={17}/></button><button aria-label="下一条本地提示" onClick={()=>setTip(i=>(i+1)%tips.length)}><ChevronRight size={17}/></button></div>
  <details className="gen-request-details"><summary>查看本轮请求信息</summary><dl><div><dt>请求阶段</dt><dd>{phase}</dd></div>{campaign.source_count!==undefined&&<div><dt>源码文件</dt><dd>{campaign.source_count} 个</dd></div>}{campaign.input_bytes!==undefined&&<div><dt>当前请求输入</dt><dd>{campaign.input_bytes.toLocaleString()} 字节</dd></div>}{campaign.wait_timeout_seconds!==undefined&&<div><dt>每段响应等待上限</dt><dd>{campaign.wait_timeout_seconds} 秒</dd></div>}</dl></details>
 </section>
}
