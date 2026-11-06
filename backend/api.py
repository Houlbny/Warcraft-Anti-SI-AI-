import os
import secrets
from pathlib import Path
from threading import Lock, Thread
from typing import Literal
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field
from .storage import Store
from .model_client import ModelClient, redact
from .prompts import load_prompt
from .sources import SourceImports
from .legacy_cleanup import cleanup_legacy
from .adventures import Adventures, ROLES
from .runner import stop_run

ROOT=Path(__file__).resolve().parent.parent
class SettingsInput(BaseModel):
    base_url:str
    model:str
    api_key:str|None=None
    generation_timeout:int|None=Field(default=None, ge=30, le=900, strict=True)
class ImportInput(BaseModel):
    path:str|None=None
    files:list[dict]|None=None
    git_url:str|None=None
    encoding:str='utf-8-sig'
class ScopeInput(BaseModel):
    paths:list[str]
    page_start:int|None=None
    page_end:int|None=None
class AnalyzeInput(BaseModel): focus:str|None=Field(default=None,max_length=2000)
class CharacterInput(BaseModel):
    role:str
    name:str|None=Field(default=None,max_length=100)
class AdventureInput(BaseModel): import_id:str
class PrepareTaskInput(BaseModel): replace:bool=False
class AnswerInput(BaseModel): answer:str=Field(max_length=12000)
class ReflectionInput(BaseModel):
    confidence:Literal['needs_review','understood','can_apply']
    note:str=Field(max_length=2000)
class FileInput(BaseModel):
    path:str
    content:str=Field(max_length=1048576)
class RunInput(BaseModel): entry:str|None=None

def create_app(data_root=None, *, use_keyring=True):
    app=FastAPI(title='学习冒险',version='0.3.0',docs_url='/api/docs',redoc_url=None)
    store=Store(Path(data_root or os.environ.get('CODEWARDEN_DATA',ROOT/'.warcraft')))
    cleanup_legacy(store)
    sources=SourceImports(store); model=ModelClient(store,use_keyring=use_keyring); domain=Adventures(store,sources,model)
    app.state.store,app.state.model,app.state.sources,app.state.adventures=store,model,sources,domain
    app.state.shutting_down=False
    jobs=set(); jobs_lock=Lock()
    origins=['http://127.0.0.1:8765','http://localhost:8765','http://localhost:5173','http://127.0.0.1:5173']
    app.add_middleware(CORSMiddleware,allow_origins=origins,allow_methods=['GET','POST','PUT','PATCH','DELETE'],allow_headers=['Content-Type'])
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['localhost','127.0.0.1','testserver'])
    @app.middleware('http')
    async def local_boundary(request:Request,call_next):
        if request.method not in {'GET','HEAD','OPTIONS'}:
            if app.state.shutting_down and request.url.path != '/api/runtime/shutdown': return JSONResponse({'detail':'服务正在关闭。'},status_code=503)
            origin=request.headers.get('origin'); same=str(request.base_url).rstrip('/')
            if (origin and origin not in origins and origin != same) or request.headers.get('sec-fetch-site')=='cross-site': return JSONResponse({'detail':'拒绝跨站本地操作。'},status_code=403)
        response=await call_next(request); response.headers['X-Content-Type-Options']='nosniff'; response.headers['Referrer-Policy']='no-referrer'; return response
    @app.exception_handler(ValueError)
    async def bad(request,exc): return JSONResponse({'detail':redact(str(exc))},status_code=409 if '冲突' in str(exc) else 400)
    @app.exception_handler(KeyError)
    async def missing(request,exc): return JSONResponse({'detail':str(exc.args[0])},status_code=404)
    @app.exception_handler(RequestValidationError)
    async def invalid(request,exc): return JSONResponse({'detail':'请求参数无效。'},status_code=422)
    def spawn(key,fn,validate=None):
        with jobs_lock:
            if app.state.shutting_down: raise HTTPException(503,'服务正在关闭。')
            if key in jobs: raise HTTPException(409,'操作正在进行，请稍候。')
            if validate: validate()
            jobs.add(key)
        def work():
            try: fn()
            finally:
                with jobs_lock: jobs.discard(key)
        Thread(target=work,daemon=True).start()
    def referenced(iid): return any(a['import_id']==iid for a in store.all('adventures_v2'))
    def validate_analysis(iid):
        sources.get(iid)
        if referenced(iid): raise ValueError('材料已分配。')
    def import_view(record):
        result=sources.public(record)
        with jobs_lock:
            if 'import:'+record['id'] in jobs: result['status']='analyzing'
        return result
    def adventure_view(aid):
        result=domain.get(aid)
        with jobs_lock:
            if 'plan:'+aid in jobs: result['status']='planning'
            for task in result['tasks']:
                if 'task:'+task['id'] in jobs: task['status']='generating'
        return result
    @app.get('/api/health')
    def health(): return {'status':'ok','version':'0.3.0'}
    def runtime_identity(request):
        info=getattr(app.state,'runtime_info',None); expected=getattr(app.state,'runtime_token',None); callback=getattr(app.state,'shutdown_callback',None)
        if not isinstance(info,dict) or not expected or not callable(callback): raise HTTPException(404,'当前服务未登记受管理的启动信息，请通过启动脚本运行。')
        provided=request.headers.get('X-Codewarden-Runtime-Token','')
        if not request.client or request.client.host not in {'127.0.0.1','::1'} or not secrets.compare_digest(provided.encode(),expected.encode()): raise HTTPException(403,'重启控制身份验证失败。')
        return {k:info[k] for k in ('schema','project_root','data_root','pid','port','instance_id','started_at') if k in info}
    @app.get('/api/runtime')
    def runtime(request:Request): return runtime_identity(request)
    @app.post('/api/runtime/shutdown')
    def shutdown(request:Request):
        info=runtime_identity(request)
        with jobs_lock:
            if jobs or domain.active(): raise HTTPException(409,'操作正在进行，请稍后重启。')
            if app.state.shutting_down: raise HTTPException(409,'服务已在关闭。')
            app.state.shutting_down=True
        return JSONResponse({'stopping':True,'instance_id':info['instance_id']},status_code=202,background=BackgroundTask(app.state.shutdown_callback))
    @app.get('/api/settings')
    def settings(): return model.public()
    @app.put('/api/settings')
    def settings_update(body:SettingsInput):
        with store.lock:
            result=model.configure(body.base_url,body.model,body.api_key,generation_timeout=body.generation_timeout)
            store.put('adventures_meta','settings_revision',store.get('adventures_meta','settings_revision',0)+1)
            return result
    @app.post('/api/settings/test')
    def settings_test(): return {'ok':True,'message':'接口连接成功：'+model.chat(load_prompt('connectivity_system'),load_prompt('connectivity_user'),purpose='connectivity')[:160]}
    @app.get('/api/roles')
    def roles(): return ROLES
    @app.get('/api/characters')
    def characters(): return domain.characters()
    @app.post('/api/characters')
    def create_character(body:CharacterInput): return domain.create_character(body.role,body.name)
    @app.delete('/api/characters/{cid}')
    def delete_character(cid:str):
        with jobs_lock:
            aids={a['id'] for a in domain.list_adventures(cid)}
            tids={t['id'] for aid in aids for t in domain.raw(aid)['tasks']}
            if any(('plan:'+aid) in jobs for aid in aids) or any(('task:'+tid) in jobs for tid in tids): raise ValueError('角色冒险正在处理，请稍后重试。')
            return domain.delete_character(cid)
    @app.get('/api/characters/{cid}/adventures')
    def character_adventures(cid:str): return domain.list_adventures(cid)
    @app.post('/api/characters/{cid}/adventures')
    def create_adventure(cid:str,body:AdventureInput):
        with jobs_lock:
            if 'import:'+body.import_id in jobs: raise ValueError('材料正在分析。')
            return domain.create_adventure(cid,body.import_id)
    @app.post('/api/imports')
    def create_import(body:ImportInput): return sources.public(sources.create(**body.model_dump()))
    @app.get('/api/imports')
    def imports(): return [import_view(r) for r in store.all('imports_v2') if not referenced(r['id'])]
    @app.get('/api/imports/{iid}')
    def get_import(iid:str): return import_view(sources.get(iid))
    @app.put('/api/imports/{iid}/scope')
    def scope(iid:str,body:ScopeInput):
        with jobs_lock:
            if referenced(iid) or 'import:'+iid in jobs: raise ValueError('材料已分配或正在分析，不能更改范围。')
            return sources.public(sources.select_scope(iid,body.paths,body.page_start,body.page_end))
    @app.post('/api/imports/{iid}/analyze',status_code=202)
    def analyze(iid:str,body:AnalyzeInput):
        source=sources.get(iid)
        if referenced(iid): raise ValueError('材料已分配，不能重新分析。')
        spawn('import:'+iid,lambda:domain.analyze(iid,body.focus),lambda:validate_analysis(iid))
        source['status']='analyzing'; return sources.public(source)
    @app.get('/api/imports/{iid}/file')
    def import_file(iid:str,path:str): return sources.read(iid,path)
    @app.delete('/api/imports/{iid}')
    def delete_import(iid:str):
        with jobs_lock:
            if referenced(iid) or 'import:'+iid in jobs: raise ValueError('材料正在使用或分析。')
            sources.delete(iid)
        return {'deleted':True}
    @app.get('/api/adventures/{aid}')
    def adventure(aid:str): return adventure_view(aid)
    @app.delete('/api/adventures/{aid}')
    def delete_adventure(aid:str):
        with jobs_lock:
            a=domain.raw(aid)
            if 'plan:'+aid in jobs or any('task:'+t['id'] in jobs for t in a['tasks']): raise ValueError('冒险正在处理，请稍后重试。')
            return domain.delete(aid)
    @app.post('/api/adventures/{aid}/prepare',status_code=202)
    def prepare(aid:str):
        a=domain.get(aid)
        if a['objectives']: return a
        spawn('plan:'+aid,lambda:domain.prepare(aid),lambda:domain.raw(aid)); a['status']='planning'; return a
    @app.get('/api/adventures/{aid}/tasks/{tid}')
    def task(aid:str,tid:str):
        return next((t for t in adventure_view(aid)['tasks'] if t['id']==tid),None) or domain.missing('任务不存在。')
    @app.post('/api/adventures/{aid}/tasks/{tid}/prepare',status_code=202)
    def prepare_task(aid:str,tid:str,body:PrepareTaskInput):
        t=domain.task(aid,tid)
        if t['learning_status']=='locked': raise ValueError('请先完成必要前置目标。')
        if t['status']=='ready' and not body.replace: return t
        spawn('task:'+tid,lambda:domain.prepare_task(aid,tid,body.replace),lambda:domain.task(aid,tid)); t['status']='generating'; return t
    @app.post('/api/adventures/{aid}/tasks/{tid}/hint')
    def hint(aid:str,tid:str): return domain.hint(aid,tid)
    @app.post('/api/adventures/{aid}/tasks/{tid}/answer')
    def answer(aid:str,tid:str,body:AnswerInput): return domain.answer(aid,tid,body.answer)
    @app.put('/api/adventures/{aid}/tasks/{tid}/reflection')
    def reflection(aid:str,tid:str,body:ReflectionInput): return domain.reflect(aid,tid,body.confidence,body.note)
    @app.get('/api/adventures/{aid}/tasks/{tid}/file')
    def task_file(aid:str,tid:str,path:str): return domain.workspace_file(aid,tid,path)
    @app.put('/api/adventures/{aid}/tasks/{tid}/file')
    def write_file(aid:str,tid:str,body:FileInput): return domain.workspace_file(aid,tid,body.path,body.content)
    @app.post('/api/adventures/{aid}/tasks/{tid}/run')
    def run(aid:str,tid:str,body:RunInput): return domain.run(aid,tid,body.entry)
    @app.post('/api/adventures/{aid}/tasks/{tid}/stop')
    def stop(aid:str,tid:str): domain.task(aid,tid); return {'stopped':stop_run(tid)}
    @app.get('/api/adventures/{aid}/journal')
    def journal(aid:str): return domain.journal(aid)
    dist=ROOT/'frontend'/'dist'
    if (dist/'assets').is_dir(): app.mount('/assets',StaticFiles(directory=dist/'assets'),name='assets')
    if (dist/'public').is_dir(): app.mount('/public',StaticFiles(directory=dist/'public'),name='public')
    @app.get('/')
    def index():
        if (dist/'index.html').is_file(): return FileResponse(dist/'index.html')
        return JSONResponse({'message':'前端尚未构建。'},status_code=503)
    return app
app=create_app()
