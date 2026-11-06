from backend.adventures import Adventures
from backend.storage import Store

class Sources:
    def __init__(self):
        self.record = {'id':'i', 'title':'test', 'snapshot_hash':'h', 'status':'analyzed', 'analysis':{'summary':'s','recommendations':[{'role':'mage'}]}}
    def get(self, i): return self.record
    def context(self, i, max_chars=24000): return [{'id':'s1','path':'a.txt','line':1,'content':'source'}]
    def selected_fragments(self, i): return self.context(i)
    def batches(self, i, **kwargs): return [{'id':'b1','sources':self.context(i),'chars':6}]
    def context_for_ids(self, i, source_ids, max_bytes=None):
        byid={s['id']:s for s in self.context(i)}
        return [byid[sid] for sid in source_ids]

class Model:
    def ask_json(self, system, user, **kwargs):
        if 'PLAN' in system:
            return {'title':'a','summary':'s','topics':[{'id':'t','title':'T','description':'d'}], 'objectives':[{'id':'o1','topic_id':'t','title':'one','description':'d','source_ids':['s1'],'prerequisite_ids':[],'required':True},{'id':'o2','topic_id':'t','title':'two','description':'d','source_ids':['s1'],'prerequisite_ids':[],'required':True}]}
        if 'TASK' in system:
            return {'title':'task','brief':'b','objective':'o','teaching':[{'title':'t','body':'b'}], 'question':'q','interaction':'explain','source_refs':['s1'],'criteria':['c'],'hints':['secret hint'],'answer_baseline':'secret answer'}
        return {'status':'passed','feedback':'ok','checks':[{'criterion':'c','passed':True,'evidence':'yes'}]}
    def public(self): return {'base_url':'test','model':'test'}

def test_independent_tasks_and_idempotent_completion(tmp_path):
    domain = Adventures(Store(tmp_path), Sources(), Model())
    char = domain.create_character('mage')
    adventure = domain.create_adventure(char['id'], 'i')
    domain.prepare(adventure['id'])
    state = domain.get(adventure['id'])
    assert len(state['tasks']) == 2
    assert all(t['learning_status'] == 'available' for t in state['tasks'])
    assert 'answer_baseline' not in str(state) and 'secret hint' not in str(state)
    task = state['tasks'][0]
    domain.answer(adventure['id'], task['id'], 'answer')
    domain.answer(adventure['id'], task['id'], 'answer')
    assert domain.get(adventure['id'])['completed_objectives'] == 1
    assert domain.characters()[0]['completed_objectives'] == 1

def test_review_without_evidence_stays_pending(tmp_path):
    class BadReview(Model):
        def ask_json(self, system, user, **kwargs):
            if 'REVIEW' in system: return {'status':'passed','feedback':'ok','checks':[]}
            return super().ask_json(system,user,**kwargs)
    d=Adventures(Store(tmp_path),Sources(),BadReview()); c=d.create_character('mage'); a=d.create_adventure(c['id'],'i'); d.prepare(a['id'])
    t=d.get(a['id'])['tasks'][0]; result=d.answer(a['id'],t['id'],'answer')
    assert result['status']=='pending'
    assert d.get(a['id'])['completed_objectives']==0

def test_incompatible_role_cannot_create_adventure(tmp_path):
    import pytest
    d=Adventures(Store(tmp_path),Sources(),Model()); c=d.create_character('priest')
    with pytest.raises(ValueError): d.create_adventure(c['id'],'i')

def test_retryable_model_request_is_bounded(tmp_path):
    from backend.model_client import RetryableModelError
    class Unavailable(Model):
        calls=0
        def ask_json(self,*args,**kwargs):
            self.calls+=1
            raise RetryableModelError('unavailable')
    m=Unavailable(); d=Adventures(Store(tmp_path),Sources(),m)
    import pytest
    with pytest.raises(RetryableModelError): d._ask('PLAN',{})
    assert m.calls==2

def test_all_five_roles_can_create_independent_characters(tmp_path):
    d=Adventures(Store(tmp_path),Sources(),Model())
    roles=['mage','paladin','druid','shaman','priest']
    chars=[d.create_character(r) for r in roles]
    assert len({c['id'] for c in chars})==5
    assert {c['role'] for c in d.characters()}==set(roles)

def test_failed_second_task_does_not_block_first(tmp_path):
    class Partial(Model):
        tasks=0
        def ask_json(self,system,user,**kwargs):
            if 'TASK' in system:
                self.tasks+=1
                if self.tasks==2: raise ValueError('unavailable')
            return super().ask_json(system,user,**kwargs)
    d=Adventures(Store(tmp_path),Sources(),Partial()); c=d.create_character('mage'); a=d.create_adventure(c['id'],'i'); d.prepare(a['id'])
    tasks=d.get(a['id'])['tasks']
    assert [t['status'] for t in tasks]==['ready','failed']
    assert d.answer(a['id'],tasks[0]['id'],'answer')['status']=='passed'

def test_single_non_code_goal_is_valid(tmp_path):
    class Single(Model):
        def ask_json(self,system,user,**kwargs):
            result=super().ask_json(system,user,**kwargs)
            if 'PLAN' in system: result['objectives']=result['objectives'][:1]
            return result
    d=Adventures(Store(tmp_path),Sources(),Single()); c=d.create_character('mage'); a=d.create_adventure(c['id'],'i'); d.prepare(a['id'])
    assert len(d.get(a['id'])['tasks'])==1
    assert d.get(a['id'])['tasks'][0]['interaction']=='explain'

def test_actual_run_binds_current_independent_workspace_version(tmp_path):
    import base64
    from backend.sources import SourceImports
    store=Store(tmp_path); sources=SourceImports(store)
    source=sources.create(files=[{'path':'main.py','data_base64':base64.b64encode(b'print(1)\n').decode()}])
    source.update(status='analyzed',analysis={'summary':'s','recommendations':[{'role':'mage'}]}); store.put('imports_v2',source['id'],source)
    class Code(Model):
        def ask_json(self,system,user,**kwargs):
            result=super().ask_json(system,user,**kwargs)
            if 'TASK' in system: result.update(interaction='code',run_entry='main.py')
            return result
    d=Adventures(store,sources,Code()); c=d.create_character('mage'); a=d.create_adventure(c['id'],source['id']); d.prepare(a['id'])
    one,two=d.get(a['id'])['tasks']; first=d.run(a['id'],one['id'])
    assert first['evidence_kind']=='local_execution' and first['exit_code']==0 and first['stdout'].strip()=='1'
    d.workspace_file(a['id'],one['id'],'main.py','print(2)\n')
    second=d.run(a['id'],one['id']); other=d.run(a['id'],two['id'])
    assert second['workspace_version']!=first['workspace_version'] and second['stdout'].strip()=='2'
    assert other['stdout'].strip()=='1'
    assert d.get(a['id'])['completed_objectives']==0

def test_review_must_cover_every_criterion(tmp_path):
    class PartialReview(Model):
        def ask_json(self,system,user,**kwargs):
            r=super().ask_json(system,user,**kwargs)
            if 'TASK' in system: r['criteria']=['c','other']
            return r
    d=Adventures(Store(tmp_path),Sources(),PartialReview()); c=d.create_character('mage'); a=d.create_adventure(c['id'],'i'); d.prepare(a['id'])
    t=d.get(a['id'])['tasks'][0]; assert d.answer(a['id'],t['id'],'answer')['status']=='pending'

def test_replacement_hides_archived_task_but_keeps_attempt(tmp_path):
    d=Adventures(Store(tmp_path),Sources(),Model()); c=d.create_character('mage'); a=d.create_adventure(c['id'],'i'); d.prepare(a['id'])
    t=d.get(a['id'])['tasks'][0]; d.answer(a['id'],t['id'],'first')
    d.prepare_task(a['id'],t['id'],replace=True)
    state=d.get(a['id'])
    assert t['id'] not in [x['id'] for x in state['tasks']]
    assert any(e['task_id']==t['id'] and e['answer']=='first' for e in d.journal(a['id'])['entries'])

def test_code_answer_requires_changed_successful_current_run(tmp_path):
    import base64
    from backend.sources import SourceImports
    store=Store(tmp_path); sources=SourceImports(store)
    source=sources.create(files=[{'path':'main.py','data_base64':base64.b64encode(b'print(1)\n').decode()}]); source.update(status='analyzed',analysis={'summary':'s','recommendations':[{'role':'mage'}]}); store.put('imports_v2',source['id'],source)
    class Code(Model):
        def ask_json(self,system,user,**kwargs):
            r=super().ask_json(system,user,**kwargs)
            if 'TASK' in system: r.update(interaction='code',run_entry='main.py')
            return r
    d=Adventures(store,sources,Code()); c=d.create_character('mage'); a=d.create_adventure(c['id'],source['id']); d.prepare(a['id']); t=d.get(a['id'])['tasks'][0]
    d.run(a['id'],t['id'])
    assert d.answer(a['id'],t['id'],'done')['status']=='pending'
    d.workspace_file(a['id'],t['id'],'main.py','print(2)\n'); d.run(a['id'],t['id'])
    d.workspace_file(a['id'],t['id'],'main.py','print(3)\n')
    assert d.answer(a['id'],t['id'],'done')['status']=='pending'
    d.run(a['id'],t['id'])
    assert d.answer(a['id'],t['id'],'done')['status']=='passed'

def test_single_goal_finishes_with_role_and_topic_ending(tmp_path):
    class Single(Model):
        def ask_json(self,system,user,**kwargs):
            r=super().ask_json(system,user,**kwargs)
            if 'PLAN' in system: r['objectives']=r['objectives'][:1]
            return r
    d=Adventures(Store(tmp_path),Sources(),Single()); c=d.create_character('mage'); a=d.create_adventure(c['id'],'i'); d.prepare(a['id'])
    d.answer(a['id'],d.get(a['id'])['tasks'][0]['id'],'answer')
    state=d.get(a['id']); assert state['status']=='completed' and '法师' in state['ending'] and 'T' in state['ending']

def test_only_consumed_hints_are_public(tmp_path):
    d=Adventures(Store(tmp_path),Sources(),Model()); c=d.create_character('mage'); a=d.create_adventure(c['id'],'i'); d.prepare(a['id']); t=d.get(a['id'])['tasks'][0]
    assert t['used_hints']==[] and 'secret hint' not in str(t)
    d.hint(a['id'],t['id']); assert d.task(a['id'],t['id'])['used_hints']==['secret hint']

def test_parallel_reviews_merge_both_results(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    class Parallel(Model):
        barrier=Barrier(2)
        def ask_json(self,system,user,**kwargs):
            if 'REVIEW' in system: self.barrier.wait(timeout=5)
            return super().ask_json(system,user,**kwargs)
    d=Adventures(Store(tmp_path),Sources(),Parallel()); c=d.create_character('mage'); a=d.create_adventure(c['id'],'i'); d.prepare(a['id'])
    tasks=d.get(a['id'])['tasks']
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda t:d.answer(a['id'],t['id'],'answer'),tasks))
    assert all(r['status']=='passed' for r in results)
    assert d.get(a['id'])['completed_objectives']==2

def test_scope_change_during_generation_prevents_publication(tmp_path):
    source=Sources()
    class Changed(Model):
        def ask_json(self,system,user,**kwargs):
            r=super().ask_json(system,user,**kwargs)
            if 'PLAN' in system: source.record['scope_revision']=1
            return r
    d=Adventures(Store(tmp_path),source,Changed()); c=d.create_character('mage'); a=d.create_adventure(c['id'],'i'); d.prepare(a['id'])
    assert d.get(a['id'])['status']=='failed'
    assert d.get(a['id'])['tasks']==[]

def test_review_payload_excludes_private_history_and_enforces_utf8_budget(tmp_path):
    import json
    class Capture(Model):
        calls=[]
        def ask_json(self,system,user,**kwargs):
            self.calls.append((system,user)); return super().ask_json(system,user,**kwargs)
    m=Capture(); d=Adventures(Store(tmp_path),Sources(),m); c=d.create_character('mage'); a=d.create_adventure(c['id'],'i'); d.prepare(a['id']); t=d.get(a['id'])['tasks'][0]
    d.answer(a['id'],t['id'],'first'); d.answer(a['id'],t['id'],'second')
    payload=json.loads([u for s,u in m.calls if 'REVIEW' in s][-1]); encoded=str(payload)
    assert all(k not in payload['task'] for k in ('attempts','last_answer','last_evaluation','hints','last_run'))
    before=len(m.calls); result=d.answer(a['id'],t['id'],'汉'*12000)
    assert result['status']=='pending' and len(m.calls)==before and ('过长' in result['feedback'] or '上限' in result['feedback'])

def test_replacement_cannot_change_criteria_or_remove_execution_requirement(tmp_path):
    class Switch(Model):
        count=0
        def ask_json(self,system,user,**kwargs):
            r=super().ask_json(system,user,**kwargs)
            if 'TASK' in system:
                self.count+=1
                if self.count==1: r.update(interaction='code',run_entry='main.py',criteria=['required'])
                if self.count>2: r.update(interaction='explain',criteria=['easier'])
            return r
    d=Adventures(Store(tmp_path),Sources(),Switch()); c=d.create_character('mage'); a=d.create_adventure(c['id'],'i'); d.prepare(a['id']); old=d.get(a['id'])['tasks'][0]
    d.prepare_task(a['id'],old['id'],replace=True); new=next(t for t in d.get(a['id'])['tasks'] if t['objective_id']==old['objective_id'])
    assert new['status']=='failed'

def test_replacement_new_id_cannot_generate_twice_concurrently(tmp_path):
    from threading import Event, Thread
    class Slow(Model):
        count=0; entered=Event(); release=Event()
        def ask_json(self,system,user,**kwargs):
            if 'TASK' in system:
                self.count+=1
                if self.count==3: self.entered.set(); self.release.wait(5)
            return super().ask_json(system,user,**kwargs)
    m=Slow(); d=Adventures(Store(tmp_path),Sources(),m); c=d.create_character('mage'); a=d.create_adventure(c['id'],'i'); d.prepare(a['id']); old=d.get(a['id'])['tasks'][0]
    thread=Thread(target=lambda:d.prepare_task(a['id'],old['id'],replace=True)); thread.start(); assert m.entered.wait(5)
    new=next(t for t in d.get(a['id'])['tasks'] if t['objective_id']==old['objective_id'])
    other=Thread(target=lambda:d.prepare_task(a['id'],new['id'])); other.start()
    other.join(.3); m.release.set(); thread.join(5); other.join(5)
    assert not thread.is_alive() and not other.is_alive() and m.count==3

def test_review_receives_current_changed_code_and_bounded_run_evidence(tmp_path):
    import base64,json
    from backend.sources import SourceImports
    class Capture(Model):
        review=None
        def ask_json(self,system,user,**kwargs):
            r=super().ask_json(system,user,**kwargs)
            if 'TASK' in system: r.update(interaction='code',run_entry='main.py')
            if 'REVIEW' in system: self.review=json.loads(user)
            return r
    store=Store(tmp_path); src=SourceImports(store); source=src.create(files=[{'path':'main.py','data_base64':base64.b64encode(b'print(1)\n').decode()}]); source.update(status='analyzed',analysis={'summary':'s','recommendations':[{'role':'mage'}]}); store.put('imports_v2',source['id'],source)
    m=Capture(); d=Adventures(store,src,m); c=d.create_character('mage'); a=d.create_adventure(c['id'],source['id']); d.prepare(a['id']); t=d.get(a['id'])['tasks'][0]
    d.workspace_file(a['id'],t['id'],'main.py','print(2)\n'); d.run(a['id'],t['id']); d.answer(a['id'],t['id'],'done')
    assert 'print(2)' in str(m.review.get('code_evidence'))
    assert m.review['run_evidence']['exit_code']==0

def test_large_run_output_stays_pending_without_truncated_review(tmp_path):
    import base64
    from backend.sources import SourceImports
    class Capture(Model):
        review_calls=0
        def ask_json(self,system,user,**kwargs):
            r=super().ask_json(system,user,**kwargs)
            if 'TASK' in system: r.update(interaction='code',run_entry='main.py')
            if 'REVIEW' in system: self.review_calls+=1
            return r
    store=Store(tmp_path); src=SourceImports(store); source=src.create(files=[{'path':'main.py','data_base64':base64.b64encode(b'print(1)\n').decode()}]); source.update(status='analyzed',analysis={'summary':'s','recommendations':[{'role':'mage'}]}); store.put('imports_v2',source['id'],source)
    m=Capture(); d=Adventures(store,src,m); c=d.create_character('mage'); a=d.create_adventure(c['id'],source['id']); d.prepare(a['id']); t=d.get(a['id'])['tasks'][0]
    d.workspace_file(a['id'],t['id'],'main.py',"print('x'*5000)\n"); d.run(a['id'],t['id']); result=d.answer(a['id'],t['id'],'done')
    assert result['status']=='pending' and '输出过长' in result['feedback'] and m.review_calls==0

def test_review_excludes_unselected_changed_files(tmp_path):
    import base64,json
    from backend.sources import SourceImports
    class Capture(Model):
        review=None
        def ask_json(self,system,user,**kwargs):
            r=super().ask_json(system,user,**kwargs)
            if 'TASK' in system: r.update(interaction='code',run_entry='main.py')
            if 'REVIEW' in system: self.review=json.loads(user)
            return r
    store=Store(tmp_path); src=SourceImports(store); files=[{'path':p,'data_base64':base64.b64encode(b'print(1)\n').decode()} for p in ('main.py','private.py')]; source=src.create(files=files); source=src.select_scope(source['id'],['main.py']); source.update(status='analyzed',analysis={'summary':'s','recommendations':[{'role':'mage'}]}); store.put('imports_v2',source['id'],source)
    m=Capture(); d=Adventures(store,src,m); c=d.create_character('mage'); a=d.create_adventure(c['id'],source['id']); d.prepare(a['id']); t=d.get(a['id'])['tasks'][0]
    d.workspace_file(a['id'],t['id'],'main.py','print(2)\n'); d.workspace_file(a['id'],t['id'],'private.py','print("private change")\n'); d.run(a['id'],t['id']); d.answer(a['id'],t['id'],'done')
    assert 'private change' not in str(m.review)

def test_unrelated_successful_entry_does_not_satisfy_required_code_entry(tmp_path):
    import base64, json
    from backend.sources import SourceImports
    class Capture(Model):
        review=None
        def ask_json(self,system,user,**kwargs):
            result=super().ask_json(system,user,**kwargs)
            if 'TASK' in system: result.update(interaction='code',run_entry='main.py')
            if 'REVIEW' in system: self.review=json.loads(user)
            return result
    store=Store(tmp_path); sources=SourceImports(store)
    files=[{'path':p,'data_base64':base64.b64encode(b'print(1)\n').decode()} for p in ('main.py','unrelated.py')]
    source=sources.create(files=files); source.update(status='analyzed',analysis={'summary':'s','recommendations':[{'role':'mage'}]}); store.put('imports_v2',source['id'],source)
    model=Capture(); d=Adventures(store,sources,model); c=d.create_character('mage'); a=d.create_adventure(c['id'],source['id']); d.prepare(a['id']); task=d.get(a['id'])['tasks'][0]
    d.workspace_file(a['id'],task['id'],'main.py','print(2)\n')
    assert d.run(a['id'],task['id'],'unrelated.py')['exit_code']==0
    result=d.answer(a['id'],task['id'],'done')
    assert result['status']=='pending' and model.review is None
    assert d.run(a['id'],task['id'],'main.py')['exit_code']==0
    assert d.answer(a['id'],task['id'],'done')['status']=='passed'
    assert model.review['task']['run_entry']=='main.py' and model.review['run_evidence']['entry']=='main.py'

def test_reflection_is_saved_prioritized_and_does_not_complete(tmp_path):
    d=Adventures(Store(tmp_path),Sources(),Model()); c=d.create_character('mage'); a=d.create_adventure(c['id'],'i'); d.prepare(a['id'])
    first,second=d.get(a['id'])['tasks']
    reflection=d.reflect(a['id'],second['id'],'needs_review','Revisit this')
    state=d.get(a['id'])
    assert state['completed_objectives']==0 and state['recommended_task_id']==second['id']
    assert d.task(a['id'],second['id'])['reflection']==reflection
    assert d.journal(a['id'])['entries'][0]['reflection']==reflection
    assert d.characters()[0]['completed_objectives']==0

def test_story_events_are_idempotent_and_optional_goals_do_not_block_ending(tmp_path):
    class Optional(Model):
        def ask_json(self,system,user,**kwargs):
            r=super().ask_json(system,user,**kwargs)
            if 'PLAN' in system: r['objectives'][1]['required']=False
            return r
    d=Adventures(Store(tmp_path),Sources(),Optional()); c=d.create_character('mage'); a=d.create_adventure(c['id'],'i'); d.prepare(a['id'])
    assert d.get(a['id'])['story_events']==[]
    t=d.get(a['id'])['tasks'][0]; d.answer(a['id'],t['id'],'first')
    state=d.get(a['id']); events=state['story_events']
    assert state['status']=='completed' and {e['id'] for e in events}=={'topic:t','ending'}
    assert d.characters()[0]['completed_adventures']==1 and d.characters()[0]['stage_title']
    d.answer(a['id'],t['id'],'again'); d.prepare_task(a['id'],t['id'],replace=True)
    replacement=next(x for x in d.get(a['id'])['tasks'] if x['objective_id']==t['objective_id'])
    d.answer(a['id'],replacement['id'],'again')
    assert d.get(a['id'])['story_events']==events
    assert d.characters()[0]['completed_adventures']==1

def test_topic_with_only_optional_objectives_requires_all_its_goals(tmp_path):
    class OptionalTopic(Model):
        def ask_json(self,system,user,**kwargs):
            r=super().ask_json(system,user,**kwargs)
            if 'PLAN' in system:
                r['topics'].append({'id':'extra','title':'extra','description':'d'})
                r['objectives'][1].update(topic_id='extra',required=False)
            return r
    d=Adventures(Store(tmp_path),Sources(),OptionalTopic()); c=d.create_character('mage'); a=d.create_adventure(c['id'],'i'); d.prepare(a['id'])
    first,second=d.get(a['id'])['tasks']; d.answer(a['id'],first['id'],'one')
    assert 'topic:extra' not in [e['id'] for e in d.get(a['id'])['story_events']]
    d.answer(a['id'],second['id'],'two')
    assert 'topic:extra' in [e['id'] for e in d.get(a['id'])['story_events']]

def test_ending_does_not_claim_unfinished_optional_topic(tmp_path):
    class OptionalTopic(Model):
        def ask_json(self,system,user,**kwargs):
            r=super().ask_json(system,user,**kwargs)
            if 'PLAN' in system:
                r['topics'].append({'id':'optional','title':'未完成之地','description':'d'})
                r['objectives'][1].update(topic_id='optional',required=False)
            return r
    d=Adventures(Store(tmp_path),Sources(),OptionalTopic()); c=d.create_character('mage'); a=d.create_adventure(c['id'],'i'); d.prepare(a['id'])
    d.answer(a['id'],d.get(a['id'])['tasks'][0]['id'],'one')
    state=d.get(a['id']); assert state['status']=='completed'
    assert '未完成之地' not in state['ending']
    assert '未完成之地' not in next(e['body'] for e in state['story_events'] if e['id']=='ending')
