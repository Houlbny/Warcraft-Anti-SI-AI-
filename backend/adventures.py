"""Independent character adventures and explicit AI generation stages."""
import copy
import hashlib
import json
import shutil
import uuid
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from .model_client import redact, RetryableModelError, ModelFormatCompatibilityError, encode_chat_request, MAX_CHAPTER_REQUEST_BYTES
from .runner import run_project, stop_run
from .prompts import load_prompt
from .sources import safe_child, safe_relative, _linked
from .languages import detect_language
from .material_analysis import MaterialAnalyzer, complete_analysis

ROLES = [
    {'id':'mage','name':'法师','focus':'文档知识','description':'理解文档、概念与流程'},
    {'id':'paladin','name':'圣骑士','focus':'脚本','description':'理解和练习脚本与操作流程'},
    {'id':'druid','name':'德鲁伊','focus':'编程基础','description':'学习程序结构与基础概念'},
    {'id':'shaman','name':'萨满祭司','focus':'工程架构与实现','description':'探索工程设计与实现'},
    {'id':'priest','name':'牧师','focus':'历史文化','description':'理解历史、文化与背景'},
]
ROLE_IDS = {r['id'] for r in ROLES}

def uid(): return uuid.uuid4().hex
def now(): return datetime.now(timezone.utc).isoformat()
def digest(value): return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
def source_identity(source): return digest([source['snapshot_hash'], source.get('scope_revision', 0), source.get('scope')])

class PlanContextError(ValueError):
    pass

class Adventures:
    def __init__(self, store, sources, model):
        self.store, self.sources, self.model = store, sources, model
        self._locks, self._guard = {}, Lock()
        # Interrupted work never resumes implicitly.
        for record in store.all('imports_v2'):
            if record.get('status') == 'analyzing':
                record.update(status='failed', analysis_complete=False, error='分析中断，已完成批次已保留，请继续分析。')
                if record.get('analysis_progress'): record['analysis_progress']['phase']='failed'
                store.put('imports_v2', record['id'], record)
        for a in store.all('adventures_v2'):
            changed = False
            if a['status'] == 'planning': a.update(status='failed', error='准备中断，请重试。'); changed = True
            for t in a['tasks']:
                if t['status'] == 'generating': t.update(status='failed', error='准备中断，请重试。'); changed = True
            if changed: store.put('adventures_v2', a['id'], a)

    def lock(self, key):
        with self._guard: return self._locks.setdefault(key, Lock())
    def active(self):
        with self._guard: return any(lock.locked() for lock in self._locks.values())
    def characters(self):
        result = self.store.all('characters_v2')
        for c in result:
            adventures = [a for a in self.store.all('adventures_v2') if a['character_id'] == c['id']]
            c['adventure_count'] = len(adventures)
            c['completed_objectives'] = sum(sum(o.get('status') == 'completed' for o in a['objectives']) for a in adventures)
            c['completed_adventures'] = sum(a.get('status')=='completed' for a in adventures)
            c['stage_title'] = '营地新旅者' if c['completed_adventures']==0 else ('营地探索者' if c['completed_adventures']<3 else '营地守护者')
        return result
    def character(self, cid):
        return next((c for c in self.characters() if c['id'] == cid), None) or self.missing('角色不存在。')
    @staticmethod
    def missing(message): raise KeyError(message)
    def create_character(self, role, name=None):
        if role not in ROLE_IDS: raise ValueError('未知职业。')
        with self.store.lock:
            existing = next((c for c in self.characters() if c['role'] == role), None)
            if existing: return existing
            c = dict(id=uid(), role=role, name=(name or next(r['name'] for r in ROLES if r['id']==role))[:100], adventure_count=0, completed_objectives=0, completed_adventures=0, stage_title='营地新旅者', created_at=now())
            self.store.put('characters_v2', c['id'], c); return c
    def list_adventures(self, cid):
        self.character(cid); return [self.public(a) for a in self.store.all('adventures_v2') if a['character_id']==cid]
    def _acquire_deletion_locks(self, adventures, stack):
        keys = sorted({key for a in adventures for key in (['plan:'+a['id']] + ['task:'+t['id'] for t in a['tasks']])})
        for key in keys:
            lock=self.lock(key)
            if not lock.acquire(blocking=False): raise ValueError('冒险正在处理，请稍后重试。')
            stack.callback(lock.release)
    def delete_character(self, cid):
        self.character(cid)
        adventures=[a for a in self.store.all('adventures_v2') if a['character_id']==cid]
        with ExitStack() as stack:
            self._acquire_deletion_locks(adventures,stack)
            for a in adventures: self._delete_record(a)
            self.store.delete_record('characters_v2', cid)
        return {'deleted':True}
    def create_adventure(self, cid, iid):
        with self.store.lock: return self._create_adventure(cid,iid)
    def _create_adventure(self, cid, iid):
        c, source = self.character(cid), self.sources.get(iid)
        if not complete_analysis(source,self.sources.batches(iid)) or c['role'] not in [r.get('role') for r in source.get('analysis',{}).get('recommendations',[])]: raise ValueError('材料尚未完成完整分析或不适配该职业。')
        if any(a['import_id']==iid for a in self.store.all('adventures_v2')): raise ValueError('该材料已分配，请重新导入以创建独立冒险。')
        a = dict(id=uid(), character_id=cid, import_id=iid, role=c['role'], title=source['title'], summary=source['analysis'].get('summary',''), status='unprepared', topics=[], objectives=[], tasks=[], recommended_task_id=None, completed_objectives=0, total_objectives=0, created_at=now(), source_hash=source_identity(source))
        self.store.put('adventures_v2', a['id'], a); return self.public(a)
    def raw(self, aid): return self.store.get('adventures_v2', aid) or self.missing('冒险不存在。')
    def public(self, a):
        a = copy.deepcopy(a)
        source=self.sources.get(a['import_id'])
        a['source_analysis_complete']=complete_analysis(source,self.sources.batches(a['import_id']))
        if source.get('analysis_progress'): a['source_analysis_progress']=copy.deepcopy(source['analysis_progress'])
        a.setdefault('story_events',[])
        a['tasks'] = [t for t in a['tasks'] if not t.get('archived')]
        for t in a['tasks']:
            t['used_hints']=t.get('hints',[])[:t.get('hints_used',0)]
            for key in ('hints','answer_baseline','generation_identity','generation_token','attempts','baseline_version'): t.pop(key, None)
        a.pop('source_hash', None); a.pop('prompt_snapshot', None); a.pop('model_identity', None)
        return a
    def get(self, aid): return self.public(self.raw(aid))
    def _delete_record(self, a):
        paths=[]
        base=self.store.root / 'practice_v2'
        if _linked(base): raise ValueError('练习目录不能是链接。')
        for t in a['tasks']:
            p=safe_child(base,t['id'])
            if p.is_dir(): paths.append(p)
        for p in paths: shutil.rmtree(p)
        self.store.delete_record('adventures_v2',a['id'])
    def delete(self, aid):
        a=self.raw(aid)
        with ExitStack() as stack:
            self._acquire_deletion_locks([a],stack)
            self._delete_record(a)
        return {'deleted':True}
    def task(self, aid, tid, *, raw=False):
        a = self.raw(aid) if raw else self.get(aid)
        return next((t for t in a['tasks'] if t['id']==tid), None) or self.missing('任务不存在。')
    def _identity(self):
        p = self.model.public()
        return digest([{k:p.get(k) for k in ('base_url','model','generation_timeout','key_set')},self.store.get('adventures_meta','settings_revision',0)])
    def _ask(self, system, payload):
        user = redact(json.dumps(payload, ensure_ascii=False))
        model_name=self.model.public().get('model','')
        if len(encode_chat_request(model_name,system,user,response_format={'type':'json_object'})) > MAX_CHAPTER_REQUEST_BYTES:
            raise ValueError('模型请求超过 32,000 字节上限；请检查过长的提示词、回答或代码和运行输出，未发送残缺内容。')
        for attempt in range(2):
            try:
                return self.model.ask_json(system, user, purpose='chapter', response_format={'type':'json_object'})
            except (RetryableModelError, ModelFormatCompatibilityError):
                if attempt == 1: raise
    def _analyzer(self):
        return MaterialAnalyzer(self.store,self.sources,self._ask,self._identity,lambda:self.model.public().get('model',''))
    def analyze(self, iid, focus=None):
        with self.lock('import:'+iid):
            try:
                self._analyzer().run(iid,focus)
            except Exception as e:
                source=self.sources.get(iid); source.update(status='failed',analysis_complete=False,error=redact(str(e)))
                source.pop('analysis',None)
                if source.get('analysis_progress'): source['analysis_progress']['phase']='failed'
                self.store.put('imports_v2',iid,source)
    def _refresh(self, a):
        completed = {o['id'] for o in a['objectives'] if o.get('status')=='completed'}
        for o in a['objectives']:
            if o['id'] not in completed: o['status'] = 'available' if set(o['prerequisite_ids']) <= completed else 'locked'
        for t in a['tasks']:
            o = next(o for o in a['objectives'] if o['id']==t['objective_id'])
            t['learning_status'] = 'completed' if o['status']=='completed' else ('locked' if o['status']=='locked' else ('practicing' if t.get('last_answer') else 'available'))
        a['completed_objectives'] = len(completed); a['total_objectives'] = len(a['objectives'])
        candidates=[t for t in a['tasks'] if not t.get('archived') and t['learning_status'] in ('available','practicing')]
        candidates.sort(key=lambda t:(t.get('reflection',{}).get('confidence')!='needs_review',t['status']!='ready'))
        a['recommended_task_id']=candidates[0]['id'] if candidates else None
        events=a.setdefault('story_events',[]); event_ids={e['id'] for e in events}
        role=next(r['name'] for r in ROLES if r['id']==a['role'])
        contributions={'mage':'整理营地文献与概念线索','paladin':'整理营地操作步骤和脚本线索','druid':'梳理营地装置的程序与状态','shaman':'梳理营地设施的结构与协作','priest':'整理营地记忆中的时间与背景'}
        for topic in a['topics']:
            related=[o for o in a['objectives'] if o['topic_id']==topic['id']]
            required=[o for o in related if o['required']] or related
            event_id='topic:'+topic['id']
            if required and any(o['status']=='completed' for o in related) and all(o['status']=='completed' for o in required) and event_id not in event_ids:
                events.append({'id':event_id,'topic_id':topic['id'],'title':topic['title']+'的探索成果','body':f"{role}完成了“{topic['title']}”的学习目标，帮助{contributions[a['role']]}。",'created_at':now()}); event_ids.add(event_id)
        required=[o for o in a['objectives'] if o['required']]
        if required and all(o['status']=='completed' for o in required):
            a['status']='completed'
            if 'ending' not in event_ids:
                achieved=[t['title'] for t in a['topics'] if 'topic:'+t['id'] in event_ids]
                detail=('关于'+'、'.join(achieved[:3])+'的收获已记录。') if achieved else '已完成目标的收获已记录。'
                a['ending']=f"{role}完成了{a['title']}的必要目标。{detail}营地可以据此继续下一段探索。"
                events.append({'id':'ending','title':'阶段完成','body':a['ending'],'created_at':now()})

    def prepare(self, aid):
        with self.lock('plan:'+aid):
            a = self.raw(aid)
            if a['objectives']: return self.get(aid)
            a['status']='planning'; a.pop('error',None); self.store.put('adventures_v2',aid,a)
            try:
                source=self.sources.get(a['import_id'])
                if not complete_analysis(source,self.sources.batches(a['import_id'])):
                    self.analyze(a['import_id'])
                    source=self.sources.get(a['import_id'])
                    if not complete_analysis(source,self.sources.batches(a['import_id'])):
                        raise ValueError('材料完整分析尚未完成；'+source.get('error','请继续准备以重试未完成批次。'))
                analyzer=self._analyzer()
                context=analyzer.planning_context(a['import_id'])
                source_ids={sid for s in context for sid in [s['id'],*s.get('related_source_ids',[])]}
                identity = self._identity()
                prompt = load_prompt('adventure_plan')
                task_prompt = load_prompt('adventure_task')
                payload={'role':a['role'],'sources':context}
                for planning_attempt in range(2):
                    plan=self._ask(prompt,payload)
                    oversized=[]
                    for objective in plan.get('objectives',[]):
                        refs=objective.get('source_ids')
                        if not isinstance(refs,list) or not refs or not all(isinstance(s,str) and s in source_ids for s in refs):
                            raise ValueError('目标或来源引用无效。')
                        original=self.sources.context_for_ids(a['import_id'],refs)
                        if len(json.dumps(original,ensure_ascii=False).encode())>16000 or not analyzer._fits(task_prompt,{'role':a['role'],'objective':{**objective,'status':'available'},'sources':original}):
                            oversized.append(objective.get('id','未命名目标'))
                    if not oversized: break
                    if planning_attempt:
                        raise PlanContextError('学习目标仍包含过多原文，请重新准备以生成更小的目标；完整材料分析已保留。')
                    payload['planning_feedback']='以下目标引用原文过多：'+','.join(str(x) for x in oversized)[:1000]+'。请拆成更小且独立的理解目标，每目标通常引用一个来源片段，保留所有知识主题。不要要求玩家缩小整份材料。'
                topics, objectives = plan['topics'], plan['objectives']
                if not topics or not objectives or len(topics)>30 or len(objectives)>60: raise ValueError('规划缺少主题或目标。')
                tids = {t['id'] for t in topics}; ids = {o['id'] for o in objectives}
                if len(tids)!=len(topics) or len(ids)!=len(objectives): raise ValueError('规划标识重复。')
                for t in topics:
                    if not all(isinstance(t.get(k),str) and t[k] for k in ('id','title','description')): raise ValueError('主题格式无效。')
                for o in objectives:
                    if not all(isinstance(o.get(k),str) and o[k] for k in ('id','topic_id','title','description')) or o['topic_id'] not in tids or not isinstance(o.get('source_ids'),list) or not o['source_ids'] or not set(o['source_ids'])<=source_ids or not isinstance(o.get('prerequisite_ids'),list) or not set(o['prerequisite_ids'])<=ids or o['id'] in o['prerequisite_ids'] or not isinstance(o.get('required'),bool): raise ValueError('目标或来源引用无效。')
                    o['status']='locked'
                done=set()
                while True:
                    new={o['id'] for o in objectives if set(o['prerequisite_ids'])<=done} - done
                    if not new: break
                    done |= new
                if done != ids: raise ValueError('目标前置存在循环。')
                if not any(o['required'] for o in objectives): raise ValueError('规划缺少必要学习目标。')
                if source_identity(self.sources.get(a['import_id'])) != a['source_hash'] or self._identity()!=identity: raise ValueError('材料或模型设置已变化，请重试。')
                a.update(title=plan.get('title') or a['title'],summary=plan.get('summary') or a['summary'],topics=topics,objectives=objectives,status='ready',model_identity=identity,prompt_snapshot={'plan':prompt,'task':task_prompt})
                a['tasks']=[dict(id=uid(), objective_id=o['id'],title=o['title'],status='unprepared',learning_status='locked',hints_used=0) for o in objectives]
                self._refresh(a); self.store.put('adventures_v2',aid,a)
            except Exception as e:
                a=self.raw(aid); a.update(status='failed',error=redact(str(e))); self.store.put('adventures_v2',aid,a); return self.public(a)
            for t in [t for t in a['tasks'] if t['learning_status']=='available'][:2]: self.prepare_task(aid,t['id'])
            return self.get(aid)
    def prepare_task(self, aid, tid, replace=False):
        with ExitStack() as locks:
            locks.enter_context(self.lock('task:'+tid))
            with self.store.lock:
                a=self.raw(aid); t=next((x for x in a['tasks'] if x['id']==tid), None) or self.missing('任务不存在。')
                if t['learning_status']=='locked': raise ValueError('请先完成必要前置目标。')
                if t['status']=='ready' and not replace: return self.public(a)
                if replace and t['status']=='ready':
                    t['archived']=True
                    replacement=dict(id=uid(),objective_id=t['objective_id'],title=t['title'],status='unprepared',learning_status=t['learning_status'],hints_used=0)
                    locks.enter_context(self.lock('task:'+replacement['id']))
                    a['tasks'].append(replacement); t=replacement; tid=t['id']
                token=uid()
                t.update(status='generating',generation_token=token); t.pop('error',None); self.store.put('adventures_v2',aid,a)
                obj=copy.deepcopy(next(o for o in a['objectives'] if o['id']==t['objective_id'])); identity=self._identity(); source_hash=a['source_hash']
            try:
                context=self.sources.context_for_ids(a['import_id'],obj['source_ids'],max_bytes=16000)
                prompt=a['prompt_snapshot']['task']
                result=self._ask(prompt, {'role':a['role'],'objective':obj,'sources':context})
                if not all(isinstance(result.get(k),str) and result[k] for k in ('title','brief','objective','question')) or result.get('interaction') not in ('explain','choice','order','code') or not isinstance(result.get('teaching'),list) or not result['teaching'] or not isinstance(result.get('criteria'),list) or not result['criteria'] or not isinstance(result.get('hints'),list) or not isinstance(result.get('answer_baseline'),str): raise ValueError('任务内容格式无效。')
                if any(not isinstance(x,dict) or not all(isinstance(x.get(k),str) and x[k] for k in ('title','body')) for x in result['teaching']) or any(not isinstance(x,str) or not x for x in result['criteria']+result['hints']) or len(set(result['criteria'])) != len(result['criteria']): raise ValueError('任务教学、标准或提示格式无效。')
                if result['interaction'] in ('choice','order') and (not isinstance(result.get('options'),list) or len(result['options'])<2 or any(not isinstance(x,dict) or not all(isinstance(x.get(k),str) and x[k] for k in ('id','text')) for x in result['options']) or len({x['id'] for x in result['options']}) != len(result['options'])): raise ValueError('互动选项格式无效。')
                if result.get('run_entry') is not None: result['run_entry']=safe_relative(result['run_entry'])
                refs=result.get('source_refs',[]); byid={s['id']:s for s in context}
                if not isinstance(refs,list) or not refs or any(not isinstance(r,str) or r not in byid for r in refs): raise ValueError('任务来源引用无效。')
                result['source_refs']=[{k:v for k,v in byid[r].items() if k in ('id','path','line','page')} for r in refs]
                allowed=('title','brief','objective','teaching','question','interaction','options','source_refs','criteria','hints','answer_baseline','run_entry')
                with self.store.lock:
                    a=self.raw(aid); t=next((x for x in a['tasks'] if x['id']==tid), None) or self.missing('任务不存在。')
                    current=next(o for o in a['objectives'] if o['id']==obj['id'])
                    if t.get('generation_token')!=token: raise ValueError('任务生成已被替换。')
                    if current != obj or source_identity(self.sources.get(a['import_id'])) != source_hash or self._identity()!=identity: raise ValueError('任务输入已变化，请重试。')
                    contract=current.get('evaluation_contract')
                    if contract:
                        if result['criteria'] != contract['criteria'] or (contract['requires_execution'] and (result['interaction'] != 'code' or result.get('run_entry') != contract.get('run_entry'))): raise ValueError('替代任务改变了公开标准或执行要求。')
                    else:
                        current['evaluation_contract']={'criteria':result['criteria'], 'requires_execution':result['interaction']=='code', 'run_entry':result.get('run_entry')}
                    t.update({k:result[k] for k in allowed if k in result}); t.pop('generation_token',None); t.update(status='ready',generation_identity=digest([identity,prompt,source_hash,obj])); self._refresh(a); self.store.put('adventures_v2',aid,a)
            except Exception as e:
                with self.store.lock:
                    a=self.raw(aid); t=next((x for x in a['tasks'] if x['id']==tid), None) or self.missing('任务不存在。')
                    if t.get('generation_token')==token:
                        t.pop('generation_token',None); t.update(status='failed',error=redact(str(e))); self._refresh(a); self.store.put('adventures_v2',aid,a)
            return self.get(aid)
    def hint(self, aid, tid):
        with self.lock('task:'+tid), self.store.lock:
            a=self.raw(aid); t=next((x for x in a['tasks'] if x['id']==tid), None) or self.missing('任务不存在。')
            if t['status']!='ready' or t['learning_status']=='locked': raise ValueError('任务尚未开放。')
            hints=t.get('hints',[]); n=t['hints_used']
            if n>=len(hints): raise ValueError('没有更多提示。')
            t['hints_used']=n+1; self.store.put('adventures_v2',aid,a); return {'hint':hints[n], 'hints_used':n+1}
    def answer(self, aid, tid, answer):
        with self.lock('task:'+tid):
            with self.store.lock:
                a=self.raw(aid); t=next((x for x in a['tasks'] if x['id']==tid), None) or self.missing('任务不存在。')
                if t['status']!='ready' or t['learning_status']=='locked': raise ValueError('任务尚未开放。')
                t['last_answer']=answer; t['updated_at']=now(); self._refresh(a); self.store.put('adventures_v2',aid,a); snapshot=copy.deepcopy(t)
            try:
                if snapshot.get('interaction') == 'code':
                    workspace = self._workspace(aid, tid)
                    version = self._workspace_version(workspace)
                    run = snapshot.get('last_run', {})
                    objective=next(o for o in a['objectives'] if o['id']==snapshot['objective_id'])
                    required_entry=objective.get('evaluation_contract',{}).get('run_entry')
                    if required_entry and run.get('entry') != required_entry: raise ValueError('请运行任务要求的入口：'+required_entry)
                    if version == snapshot.get('baseline_version') or run.get('workspace_version') != version or run.get('status') != 'passed' or run.get('exit_code') != 0:
                        raise ValueError('请修改练习副本并对当前版本完成成功运行。')
                fields=('title','brief','objective','teaching','question','interaction','options','criteria','answer_baseline','run_entry')
                payload={'task':{k:snapshot[k] for k in fields if k in snapshot},'answer':answer,'sources':self.sources.context_for_ids(a['import_id'],next(o for o in a['objectives'] if o['id']==snapshot['objective_id'])['source_ids'],max_bytes=16000)}
                if snapshot.get('interaction')=='code':
                    payload['code_evidence']=self._changed_code(a['import_id'],workspace)
                    run_fields=('entry','status','command','exit_code','stdout','stderr','duration_ms','workspace_version','evidence_kind')
                    payload['run_evidence']={k:run[k] for k in run_fields if k in run}
                    if len(json.dumps(payload['run_evidence'],ensure_ascii=False).encode('utf-8'))>4096: raise ValueError('运行输出过长，请缩小输出后重新运行；未发送残缺证据。')
                result=self._ask(load_prompt('adventure_review_v2'), payload)
                if snapshot.get('interaction')=='code' and self._workspace_version(workspace)!=version: raise ValueError('评审期间练习副本已变化，请重新运行并提交。')
                if result.get('status') not in ('passed','needs_work') or not isinstance(result.get('feedback'),str) or not isinstance(result.get('checks'),list) or not result['checks'] or (result['status']=='passed' and not all(c.get('passed') is True for c in result['checks'] if isinstance(c,dict))) or any(not isinstance(c,dict) or not isinstance(c.get('passed'),bool) or not isinstance(c.get('criterion'),str) or not isinstance(c.get('evidence'),str) for c in result['checks']): raise ValueError('评审格式无效。')
                criteria=snapshot['criteria']; checks=result['checks']
                if len(checks)!=len(criteria) or len(set(criteria))!=len(criteria) or {c['criterion'] for c in checks}!=set(criteria): raise ValueError('评审未逐项覆盖公开标准。')
            except Exception as e: result={'status':'pending','feedback':'评审暂不可用：'+redact(str(e)),'checks':[]}
            result['evaluation']='ai_review'
            with self.store.lock:
                a=self.raw(aid); t=next((x for x in a['tasks'] if x['id']==tid), None) or self.missing('任务不存在。'); t['last_evaluation']=result
                t.setdefault('attempts',[]).append({'answer':answer,'feedback':result['feedback'],'status':result['status'],'hints_used':t['hints_used'],'updated_at':t['updated_at']})
                if result['status']=='passed': next(o for o in a['objectives'] if o['id']==t['objective_id'])['status']='completed'
                self._refresh(a); self.store.put('adventures_v2',aid,a)
                result=copy.deepcopy(result); result['task']=self.task(aid,tid); return result
    def reflect(self, aid, tid, confidence, note):
        if confidence not in ('needs_review','understood','can_apply') or not isinstance(note,str) or len(note)>2000: raise ValueError('自评或笔记无效。')
        with self.lock('task:'+tid), self.store.lock:
            a=self.raw(aid); t=next((x for x in a['tasks'] if x['id']==tid and not x.get('archived')), None) or self.missing('任务不存在。')
            reflection={'confidence':confidence,'note':note,'updated_at':now()}
            t['reflection']=reflection; self._refresh(a); self.store.put('adventures_v2',aid,a)
            return copy.deepcopy(reflection)
    def journal(self, aid):
        a=self.raw(aid); entries=[]
        for t in a['tasks']:
            reflection=t.get('reflection')
            attempts=t.get('attempts',[])
            for attempt in attempts:
                entry=dict(task_id=t['id'],title=t['title'],**attempt)
                if reflection: entry['reflection']=copy.deepcopy(reflection)
                entries.append(entry)
            if reflection and not attempts:
                entries.append(dict(task_id=t['id'],title=t['title'],answer='',feedback='',status='pending',hints_used=t['hints_used'],updated_at=reflection['updated_at'],reflection=copy.deepcopy(reflection)))
        return {'entries':entries}
    def _changed_code(self, iid, workspace):
        changed=[]
        record=self.sources.get(iid)
        selected=set(record.get('scope',{}).get('paths',[f['path'] for f in record['files']]))
        for item in record['files']:
            path=item['path']
            if path not in selected: continue
            target=safe_child(workspace,path)
            if not target.is_file(): continue
            try:
                content=target.read_text(encoding='utf-8-sig')
                original=self.sources.read(iid,path)['content']
            except (UnicodeError,OSError): raise ValueError('练习文件无法按文本读取，请检查后重试。')
            if content!=original: changed.append({'path':path,'content':content})
        if not changed: raise ValueError('没有可评审的练习文件改动。')
        if len(json.dumps(changed,ensure_ascii=False).encode('utf-8'))>12000: raise ValueError('修改后的代码过长，请缩小修改范围后重试；未发送残缺代码。')
        return changed
    def _workspace_version(self, workspace):
        return digest([(p.relative_to(workspace).as_posix(),hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(workspace.rglob('*')) if p.is_file() and not _linked(p) and not any(part in ('.runner','__pycache__') for part in p.relative_to(workspace).parts)])
    def _workspace(self, aid, tid):
        a=self.raw(aid); self.task(aid,tid)
        workspace=self.sources.workspace(a['import_id'],tid).resolve()
        with self.store.lock:
            a=self.raw(aid); t=next((x for x in a['tasks'] if x['id']==tid), None) or self.missing('任务不存在。')
            if 'baseline_version' not in t:
                t['baseline_version']=self._workspace_version(workspace); self.store.put('adventures_v2',aid,a)
        return workspace
    def _workspace_file(self, aid, tid, path, content=None):
        workspace=self._workspace(aid,tid)
        target=safe_child(workspace,path,exists=True)
        if content is not None: target.write_text(content,encoding='utf-8')
        value=target.read_text(encoding='utf-8-sig'); return {'path':path,'content':value,'language':detect_language(path),'hash':hashlib.sha256(target.read_bytes()).hexdigest()}
    def workspace_file(self, aid, tid, path, content=None):
        with self.lock('task:'+tid): return self._workspace_file(aid,tid,path,content)
    def run(self, aid, tid, entry=None):
        with self.lock('task:'+tid):
            a=self.raw(aid); t=next((x for x in a['tasks'] if x['id']==tid), None) or self.missing('任务不存在。')
            if t['status']!='ready' or t['learning_status']=='locked' or t.get('interaction')!='code': raise ValueError('此任务不能运行。')
            entry=entry or t.get('run_entry')
            if not entry: raise ValueError('请选择运行入口。')
            entry=safe_relative(entry)
            self._workspace_file(aid,tid,entry)
            workspace=self._workspace(aid,tid)
            version=self._workspace_version(workspace)
            result=run_project(workspace,entry,run_key=tid,timeout=30); result.update(id=uid(),entry=entry,workspace_version=version,evidence_kind='local_execution')
            result['stdout']=redact(result.get('stdout','')); result['stderr']=redact(result.get('stderr',''))
            if self._workspace_version(workspace) != version:
                result['status']='failed'; result['stderr'] += '\n运行期间练习文件发生变化，结果不能证明当前版本。'
            with self.store.lock:
                a=self.raw(aid); t=next((x for x in a['tasks'] if x['id']==tid), None) or self.missing('任务不存在。'); t['last_run']=result; self.store.put('adventures_v2',aid,a)
            return result
