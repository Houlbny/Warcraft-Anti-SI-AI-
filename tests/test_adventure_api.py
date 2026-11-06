from fastapi.testclient import TestClient
from backend.api import create_app

def test_roles_and_old_routes_are_not_exposed(tmp_path):
    with TestClient(create_app(tmp_path, use_keyring=False)) as client:
        assert {r['id'] for r in client.get('/api/roles').json()} == {'mage','paladin','druid','shaman','priest'}
        a = client.post('/api/characters', json={'role':'priest'}).json()
        b = client.post('/api/characters', json={'role':'priest'}).json()
        assert a['id'] == b['id']
        assert client.get('/api/knowledge').status_code == 404
        assert client.get('/api/projects').status_code == 404

def test_scope_is_public_and_invalidates_analysis(tmp_path):
    import base64
    with TestClient(create_app(tmp_path, use_keyring=False)) as client:
        files=[{'path':p,'data_base64':base64.b64encode(p.encode()).decode()} for p in ('a.txt','b.txt')]
        source=client.post('/api/imports',json={'files':files}).json()
        assert 'fragments' not in source
        result=client.put('/api/imports/'+source['id']+'/scope',json={'paths':['a.txt']})
        assert result.status_code==200
        assert result.json()['scope']['paths']==['a.txt']
        assert result.json()['status']=='imported'

def test_reflection_endpoint_validates_and_preserves_progress(tmp_path):
    app=create_app(tmp_path,use_keyring=False)
    # An adventure always owns a real import. Progress now also exposes the
    # analysis state, so this fixture includes its source instead of a dangling ID.
    app.state.store.put('imports_v2','i',{'id':'i','title':'Source','snapshot_hash':'snapshot',
        'status':'analyzed','analysis_complete':True,'analysis':{'summary':'s'},
        'fragments':[{'id':'s1','path':'source.md','line':1,'content':'Concept'}],
        'scope':{'paths':['source.md']},'files':[{'path':'source.md'}]})
    app.state.store.put('adventures_v2','a',{'id':'a','character_id':'c','import_id':'i','role':'mage','title':'A','summary':'s','status':'ready','topics':[{'id':'t','title':'T','description':'d'}],'objectives':[{'id':'o','topic_id':'t','title':'O','description':'d','source_ids':['s1'],'prerequisite_ids':[],'required':True,'status':'available'}],'tasks':[{'id':'task','objective_id':'o','title':'O','status':'ready','learning_status':'available','hints_used':0}],'recommended_task_id':'task','completed_objectives':0,'total_objectives':1,'created_at':'now'})
    with TestClient(app) as client:
        url='/api/adventures/a/tasks/task/reflection'
        assert client.put(url,json={'confidence':'bogus','note':''}).status_code==422
        assert client.put(url,json={'confidence':'understood','note':'x'*2001}).status_code==422
        response=client.put(url,json={'confidence':'needs_review','note':'look again'})
        assert response.status_code==200 and response.json()['note']=='look again'
        state=client.get('/api/adventures/a').json()
        assert state['completed_objectives']==0 and state['tasks'][0]['reflection']==response.json()
        assert client.get('/api/adventures/a/journal').json()['entries'][0]['reflection']==response.json()
