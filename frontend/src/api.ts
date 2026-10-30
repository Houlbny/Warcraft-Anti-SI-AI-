export type Role = 'mage' | 'paladin' | 'druid';
export type Project={id:string;name:string;source_path:string;workspace:string;languages:string[];file_count:number;created_at:string;stale:boolean;demo:boolean;completed:number;total:number};
export type FileInfo={path:string;language:string;size:number;symbols:{name:string;kind:string;line:number;end_line:number}[]};
export type Quest={id:string;type:'explore'|'predict'|'repair'|'feature';title:string;brief:string;objective:string;source_file:string;line:number;expected_output?:string;status:'locked'|'available'|'completed';hints_used:number;origin:'demo'|'ai';verification:string;order:number;verification_mode:'answer'|'explain'|'run'|'review';steps:{title:string;instruction:string}[];concepts:string[];success_message:string;run_entry?:string};
export type GenerationProgress={stage:'analyzing'|'planning'|'quests'|'checking';analysis_completed:number;analysis_total:number;quests_completed:number;quests_total:number;current_quest:number|null;quest_titles:{title:string;status:'pending'|'generating'|'saved'}[];resumable:boolean;message:string};
export type Campaign={status:'unprepared'|'generating'|'failed'|'ready'|'completed';role:Role|null;title:string;summary:string;quests:Quest[];current_quest_id:string|null;completed:number;total:number;error?:string;coverage?:string;started_at?:string;phase?:'preparing'|'requesting'|'validating';source_count?:number;input_bytes?:number;wait_timeout_seconds?:number;generation?:GenerationProgress};
export type Evaluation={correct:boolean;feedback:string;checks?:{criterion:string;passed:boolean;evidence:string}[];quest:Quest;evaluation?:'ai_review'};
export type Source={path:string;content:string;original:string;hash:string;language:string};
export type Run={id?:string;status:string;command:string[];exit_code:number|null;stdout:string;stderr:string;duration_ms:number;quest_completed?:boolean};
export type Card={id:string;language:string;title:string;summary:string;body:string;code:string;output:string;pitfalls:string[];tags:string[];version:string;source_url:string;reviewed_at:string;favorite?:boolean};
export type Settings={base_url:string;model:string;key_set:boolean;storage:string;generation_timeout:number;storage_error?:string};
export async function api<T>(url:string,method='GET',data?:unknown):Promise<T>{const response=await fetch('/api'+url,{method,headers:{'Content-Type':'application/json'},...(data===undefined?{}:{body:JSON.stringify(data)})});if(!response.ok){let detail=`请求失败 (${response.status})`;try{const error=await response.json();detail=typeof error.detail==='string'?error.detail:JSON.stringify(error.detail)}catch{}throw new Error(detail)}return response.json()}
export const languages=[['python','Python'],['javascript','JavaScript'],['typescript','TypeScript'],['java','Java'],['go','Go'],['shell','Shell']];
export const questLabels={explore:'探索',predict:'预测',repair:'修复',feature:'扩展'};
export const roles:{id:Role;name:string;focus:string;description:string;details:string[]}[]=[
 {id:'mage',name:'法师',focus:'初学语言 · 逐步提示',description:'从当前项目的语法与小段逻辑开始，先预测，再做最小修改。',details:['代码中的语言概念','具体的观察与操作步骤','更充足的分级线索']},
 {id:'paladin',name:'圣骑士',focus:'结构原理 · 核心功能',description:'沿入口、依赖和调用链理解模块职责，解释核心功能如何工作。',details:['入口与模块之间的联系','实现链路与设计原因','带源码依据的原理解释']},
 {id:'druid',name:'德鲁伊',focus:'全局理解 · 功能扩展',description:'先看整体结构与约束，再完成一个有边界的小扩展并验证回归。',details:['全局结构与项目约束','功能修改与边界条件','实际运行与差异评审']}
];
export const runStatus:Record<string,string>={passed:'运行通过',failed:'运行失败',missing_tool:'缺少运行工具',blocked:'执行已阻止',timeout:'执行超时',cancelled:'已停止'};
