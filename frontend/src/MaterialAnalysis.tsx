import type {AnalysisProgress,ImportRecord,Role} from './api';

const roleNames:Record<Role,string>={mage:'法师',paladin:'圣骑士',druid:'德鲁伊',shaman:'萨满祭司',priest:'牧师'};
const number=(value:number)=>value.toLocaleString('zh-CN');

export function analysisReady(record:ImportRecord){
 return record.status==='analyzed'&&record.analysis_complete===true&&!!record.analysis;
}

export function analysisStatus(record:ImportRecord){
 if(record.status==='analyzing')return '分析中';
 if(analysisReady(record))return '分析完成';
 if(record.status==='failed')return '分析未完成';
 return record.status==='analyzed'?'待补全分析':'等待分析';
}

export function AnalysisProgressView({progress,complete=false}:{progress:AnalysisProgress;complete?:boolean}){
 const heading=complete?'全量分析完成':progress.phase==='summarizing'?'正在汇总材料结构与职业建议':progress.phase==='failed'?'分析暂未完成':'正在分批分析原文';
 const readingFinished=progress.total_batches>0&&progress.completed_batches===progress.total_batches;
 return <section className="analysis-progress" aria-label="材料分析进度">
  <p role="status"><strong>{heading}</strong><span>已分析 {progress.completed_batches} / {progress.total_batches} 批 · {number(progress.processed_chars)} / {number(progress.total_chars)} 字符</span></p>
  <progress aria-label="原文批次进度" max={Math.max(1,progress.total_batches)} value={progress.completed_batches}/>
  {progress.phase==='reading'&&progress.current_batch!==undefined&&!complete&&<p className="muted">当前正在处理第 {progress.current_batch} 批。</p>}
  {progress.phase==='summarizing'&&!complete&&<p className="muted">所选原文已逐批处理，正在合并分析结果；汇总完成后显示职业建议。</p>}
  {progress.phase==='failed'&&!complete&&<p className="muted">{readingFinished?'原文批次已全部处理，汇总仍未完成。':progress.failed_batches>0?`${progress.failed_batches} 批处理失败，材料尚未全部分析。`:'材料尚未全部分析。'}已完成的批次已保存，可以继续未完成的步骤。</p>}
 </section>;
}

export default function MaterialAnalysis({record,currentRole,focus,setFocus,busy,onAnalyze,onStart}:{record:ImportRecord;currentRole?:Role;focus:string;setFocus:(value:string)=>void;busy:boolean;onAnalyze:()=>void;onStart:(role:Role)=>void}){
 const ready=analysisReady(record);
 const running=record.status==='analyzing';
 const progress=record.analysis_progress;
 const resumable=record.status==='failed'&&!!progress?.completed_batches;
 const buttonText=running?'分析中…':resumable?'继续分析':record.status==='failed'?'重试分析':record.status==='analyzed'?'重新分析':'分析材料';
 return <section className="material-analysis" aria-label="分析所选材料">
  {record.coverage&&<p className="muted">所选范围共 {number(record.coverage.selected_chars)} 字符。{progress?`本次自动分批处理共 ${progress.total_batches} 批，覆盖所选范围内的全部可读文字。`:record.coverage.batch_count!==undefined?`系统将自动分批处理所选范围内的全部可读文字，预计 ${record.coverage.batch_count} 批，实际批次数会按请求大小自动调整。`:'系统会自动分批处理所选范围内的全部可读文字。'}</p>}
  {!running&&!ready&&!progress&&<p className="muted">点击“分析材料”后才会调用已配置的模型，按批读取并汇总材料。</p>}
  {progress&&<AnalysisProgressView progress={progress} complete={ready}/>}
  {record.status==='analyzed'&&!ready&&<p className="notice">这份材料的旧分析未确认覆盖全部所选内容，请重新分析后选择职业。</p>}
  <label>关注重点（可选）<input value={focus} maxLength={2000} disabled={running||busy} onChange={event=>setFocus(event.target.value)} placeholder="例如：理解关键概念"/></label>
  {resumable&&<p className="muted">保持材料范围、关注重点和模型设置不变时，会复用已完成的批次。</p>}
  <button className="primary" disabled={busy||running} onClick={onAnalyze}>{buttonText}</button>
  {ready&&!busy&&record.analysis&&<>
   <h3>AI 分析结果</h3><p>{record.analysis.summary}</p><p className="muted">材料类型：{record.analysis.content_type}</p>
   <h3>AI 推荐职业</h3>
   {currentRole&&!record.analysis.recommendations.some(recommendation=>recommendation.role===currentRole)&&<p className="notice">这份材料更适合{record.analysis.recommendations.map(recommendation=>roleNames[recommendation.role]).join('、')}，当前{roleNames[currentRole]}不擅长这类探索；可使用推荐职业的独立角色。已有角色的进度会保留。</p>}
   <p>选择已有角色或创建对应职业的独立角色。</p>
   <div className="recommendations">{record.analysis.recommendations.map(recommendation=><div key={recommendation.role}>
    <strong>{roleNames[recommendation.role]}</strong><p>{recommendation.reason}</p><small>{recommendation.focus}</small>
    <button disabled={busy} onClick={()=>onStart(recommendation.role)}>使用{roleNames[recommendation.role]}探索</button>
   </div>)}</div>
  </>}
 </section>;
}
