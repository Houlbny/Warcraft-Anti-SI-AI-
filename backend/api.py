import os
import secrets
import uuid
from pathlib import Path
from threading import Lock
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field

from .storage import Store
from .projects import Projects, digest
from .model_client import ModelClient, redact
from .lessons import Lessons
from .languages import demo_path, demo_quests, suggest_entry
from .knowledge import list_cards
from .runner import run_project, stop_run
from .campaigns import Campaigns

ROOT = Path(__file__).resolve().parent.parent


class ImportInput(BaseModel):
    path: str | None = None
    git_url: str | None = None


class DemoInput(BaseModel):
    language: str = 'python'


class SettingsInput(BaseModel):
    base_url: str
    model: str
    api_key: str | None = None
    generation_timeout: int | None = Field(default=None, ge=30, le=900, strict=True)


class FileInput(BaseModel):
    path: str
    content: str = Field(max_length=1_048_576)
    expected_hash: str | None = None


class RunInput(BaseModel):
    quest_id: str | None = None
    entry: str | None = None


class AnswerInput(BaseModel):
    answer: str = Field(max_length=12000)


class CampaignInput(BaseModel):
    role: Literal['mage', 'paladin', 'druid']


class ReviewInput(BaseModel):
    answer: str = Field(default='', max_length=12000)


class FavoriteInput(BaseModel):
    favorite: bool


def create_app(data_root=None, *, use_keyring=True):
    app = FastAPI(title='修复者营地 · Codewarden', version='0.2.0', docs_url='/api/docs', redoc_url=None)
    store = Store(Path(data_root or os.environ.get('CODEWARDEN_DATA', ROOT / '.warcraft')))
    projects = Projects(store)
    projects.recover_deletions()
    model = ModelClient(store, use_keyring=use_keyring)
    lessons = Lessons(store, projects, model)
    campaigns = Campaigns(store, projects, model, lessons)
    for project in store.all('projects'):
        saved = store.get('campaigns', project['id'])
        if saved and saved['status'] == 'generating':
            interrupted = {**saved, 'status': 'failed', 'error': '上次生成被中断，请重试；练习文件未改变。'}
            if isinstance(saved.get('generation'), dict):
                resumable = bool(store.get('campaign_drafts', project['id']))
                message = '上次生成被中断，已保存的分段可以继续；练习文件未改变。' if resumable else interrupted['error']
                interrupted['error'] = message
                interrupted['generation'] = {**saved['generation'], 'resumable': resumable, 'message': message,
                    'quest_titles': [{**q, 'status': 'pending' if q.get('status') == 'generating' else q.get('status', 'pending')} for q in saved['generation'].get('quest_titles', [])]}
            store.put('campaigns', project['id'], interrupted)
    app.state.store, app.state.projects, app.state.model = store, projects, model
    app.state.shutting_down = False
    operations, operations_lock = {}, Lock()
    origins = ['http://127.0.0.1:8765', 'http://localhost:8765', 'http://localhost:5173', 'http://127.0.0.1:5173']
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=['GET', 'POST', 'PUT', 'DELETE'], allow_headers=['Content-Type'])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1', 'testserver'])

    @app.middleware('http')
    async def local_boundary(request: Request, call_next):
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            origin = request.headers.get('origin')
            same_origin = str(request.base_url).rstrip('/')
            if origin and origin not in origins and origin != same_origin:
                return JSONResponse({'detail': '拒绝来自其他网站的本地操作。'}, status_code=403)
            if request.headers.get('sec-fetch-site') == 'cross-site':
                return JSONResponse({'detail': '拒绝跨站请求。'}, status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        status = 409 if '冲突' in str(exc) else 400
        return JSONResponse({'detail': redact(str(exc))}, status_code=status)

    @app.exception_handler(KeyError)
    async def not_found(request, exc):
        return JSONResponse({'detail': str(exc.args[0])}, status_code=404)

    def operation(project_id):
        with operations_lock:
            if app.state.shutting_down:
                raise HTTPException(503, '服务正在重启，请稍后再操作。')
            lock = operations.setdefault(project_id, Lock())
            if not lock.acquire(blocking=False):
                raise HTTPException(409, '这个项目正在生成章节、运行或修复，请等待当前操作完成。')
        return lock

    def public_project(p):
        return {k: v for k, v in p.items() if k not in {'snapshot', 'demo_language'}}

    def execute(project_id, entry=None, quest_id=None):
        project = projects.get(project_id)
        if quest_id:
            _, quest = lessons.ensure_available(project_id, quest_id)
            required_entry = quest.get('run_entry', quest['source_file'])
            if entry and entry != required_entry:
                raise ValueError('运行入口与当前任务的源码不一致，请打开任务源码后验收。')
            entry = required_entry
        entry = entry or suggest_entry(projects.files(project_id))
        if not entry or entry not in project['snapshot']:
            raise ValueError('未找到可运行入口，请先选择项目中的源码文件。')
        before = lessons.workspace_version(project_id)
        result = run_project(Path(project['workspace']), entry, run_key=project_id, timeout=30)
        result['stdout'] = redact(result.get('stdout', ''))
        result['stderr'] = redact(result.get('stderr', ''))
        result['id'] = uuid.uuid4().hex
        result['entry'] = entry
        result['quest_id'] = quest_id
        result['workspace_version'] = before
        result['quest_completed'] = False
        if lessons.workspace_version(project_id) == before:
            result['quest_completed'] = lessons.grade_run(project_id, quest_id, result)
        else:
            result['stderr'] += '\n运行期间源码发生变化，未更新任务完成状态。'
        history = store.get('runs', project_id, [])
        store.put('runs', project_id, [result] + history[:19])
        return result

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'version': '0.2.0'}

    def runtime_identity(request):
        info = getattr(app.state, 'runtime_info', None)
        expected = getattr(app.state, 'runtime_token', None)
        callback = getattr(app.state, 'shutdown_callback', None)
        if not isinstance(info, dict) or not expected or not callable(callback):
            raise HTTPException(404, '当前服务未登记受管理的启动信息，请通过启动脚本运行。')
        provided = request.headers.get('X-Codewarden-Runtime-Token', '')
        if (not request.client or request.client.host not in {'127.0.0.1', '::1'}
                or not secrets.compare_digest(provided.encode('utf-8'), expected.encode('utf-8'))):
            raise HTTPException(403, '重启控制身份验证失败。')
        return {key: info[key] for key in ('schema', 'project_root', 'data_root', 'pid', 'port', 'instance_id', 'started_at') if key in info}

    @app.get('/api/runtime')
    def runtime(request: Request):
        return runtime_identity(request)

    @app.post('/api/runtime/shutdown')
    def shutdown(request: Request):
        info = runtime_identity(request)
        with operations_lock:
            if any(lock.locked() for lock in operations.values()):
                raise HTTPException(409, '项目正在生成、运行或修复，请等待完成后再重启。')
            if app.state.shutting_down:
                raise HTTPException(409, '服务已在关闭，请等待重启完成。')
            app.state.shutting_down = True
        return JSONResponse({'stopping': True, 'instance_id': info['instance_id']}, status_code=202,
                            background=BackgroundTask(app.state.shutdown_callback))

    @app.get('/api/settings')
    def settings():
        return model.public()

    @app.put('/api/settings')
    def update_settings(body: SettingsInput):
        return model.configure(body.base_url, body.model, body.api_key, generation_timeout=body.generation_timeout)

    @app.post('/api/settings/test')
    def test_settings():
        reply = model.chat('Reply briefly to confirm connectivity.', 'Reply with OK.', purpose='connectivity')
        return {'ok': True, 'message': '接口连接成功：' + reply[:160]}

    @app.get('/api/projects')
    def all_projects():
        return [public_project(p) for p in projects.list()]

    @app.post('/api/projects/import')
    def import_project(body: ImportInput):
        if bool(body.path) == bool(body.git_url):
            raise ValueError('请填写一个本地路径或 Git 地址。')
        project = projects.import_git(body.git_url) if body.git_url else projects.import_path(body.path)
        return public_project(project)

    @app.post('/api/projects/demo')
    def import_demo(body: DemoInput):
        with projects.lock:
            project = projects.import_path(str(demo_path(body.language)), demo=True, language=body.language)
            store.put('quests', project['id'], demo_quests(body.language))
            raw = store.get('projects', project['id'])
            names = {'python': 'Python', 'java': 'Java', 'javascript': 'JavaScript', 'typescript': 'TypeScript', 'go': 'Go', 'shell': 'Bash / sh'}
            raw['name'] = names[body.language] + ' · 药水仓库'
            store.put('projects', project['id'], raw)
            return public_project(projects.get(project['id']))

    @app.get('/api/projects/{project_id}')
    def get_project(project_id: str):
        return public_project(projects.get(project_id))

    @app.post('/api/projects/{project_id}/refresh')
    def refresh(project_id: str):
        lock = operation(project_id)
        try:
            return public_project(projects.refresh(project_id))
        finally:
            lock.release()

    @app.delete('/api/projects/{project_id}')
    def delete_project(project_id: str):
        lock = operation(project_id)
        try:
            return projects.delete(project_id)
        finally:
            lock.release()

    @app.get('/api/projects/{project_id}/files')
    def files(project_id: str):
        return projects.files(project_id)

    @app.get('/api/projects/{project_id}/file')
    def file(project_id: str, path: str):
        return projects.read_file(project_id, path)

    @app.put('/api/projects/{project_id}/file')
    def write_file(project_id: str, body: FileInput):
        lock = operation(project_id)
        try:
            return projects.write_file(project_id, body.path, body.content, body.expected_hash)
        finally:
            lock.release()

    @app.get('/api/projects/{project_id}/quests')
    def quests(project_id: str):
        projects.get(project_id)
        return lessons.list(project_id)

    @app.get('/api/projects/{project_id}/campaign')
    def campaign(project_id: str):
        return campaigns.get(project_id)

    @app.post('/api/projects/{project_id}/campaign')
    def prepare_campaign(project_id: str, body: CampaignInput):
        lock = operation(project_id)
        try:
            return campaigns.generate(project_id, body.role)
        finally:
            lock.release()

    @app.post('/api/projects/{project_id}/generate')
    def generate(project_id: str):
        lock = operation(project_id)
        try:
            return lessons.generate(project_id)
        finally:
            lock.release()

    @app.post('/api/projects/{project_id}/quests/{quest_id}/answer')
    def answer(project_id: str, quest_id: str, body: AnswerInput):
        projects.get(project_id)
        lock = operation(project_id)
        try:
            return lessons.answer(project_id, quest_id, body.answer)
        finally:
            lock.release()

    @app.post('/api/projects/{project_id}/quests/{quest_id}/review')
    def review_quest(project_id: str, quest_id: str, body: ReviewInput):
        projects.get(project_id)
        lock = operation(project_id)
        try:
            return lessons.review(project_id, quest_id, body.answer)
        finally:
            lock.release()

    @app.post('/api/projects/{project_id}/quests/{quest_id}/hint')
    def hint(project_id: str, quest_id: str):
        projects.get(project_id)
        lock = operation(project_id)
        try:
            return lessons.hint(project_id, quest_id)
        finally:
            lock.release()

    @app.post('/api/projects/{project_id}/run')
    def run(project_id: str, body: RunInput):
        lock = operation(project_id)
        try:
            return execute(project_id, body.entry, body.quest_id)
        finally:
            lock.release()

    @app.post('/api/projects/{project_id}/stop')
    def stop(project_id: str):
        projects.get(project_id)
        return {'stopped': stop_run(project_id)}

    @app.get('/api/projects/{project_id}/runs')
    def runs(project_id: str):
        projects.get(project_id)
        return store.get('runs', project_id, [])

    @app.post('/api/projects/{project_id}/repair')
    def repair(project_id: str):
        from .repairs import Repairs
        lock = operation(project_id)
        try:
            return Repairs(store, projects, model, execute).repair(project_id)
        finally:
            lock.release()

    @app.post('/api/projects/{project_id}/undo-repair')
    def undo_repair(project_id: str):
        from .repairs import Repairs
        lock = operation(project_id)
        try:
            return Repairs(store, projects, model, execute).undo(project_id)
        finally:
            lock.release()

    @app.get('/api/knowledge')
    def knowledge(q: str = '', language: str = ''):
        favorites = store.get('preferences', 'favorites', [])
        return [{**card, 'favorite': card['id'] in favorites} for card in list_cards(q, language)]

    @app.put('/api/knowledge/{card_id}/favorite')
    def favorite(card_id: str, body: FavoriteInput):
        if not any(card['id'] == card_id for card in list_cards()):
            raise KeyError('资料不存在。')
        with store.lock:
            favorites = set(store.get('preferences', 'favorites', []))
            (favorites.add if body.favorite else favorites.discard)(card_id)
            store.put('preferences', 'favorites', sorted(favorites))
        return {'ok': True}

    dist = ROOT / 'frontend' / 'dist'
    if (dist / 'assets').is_dir():
        app.mount('/assets', StaticFiles(directory=dist / 'assets'), name='assets')

    @app.get('/')
    def index():
        if (dist / 'index.html').is_file():
            return FileResponse(dist / 'index.html')
        return JSONResponse({'message': '前端尚未构建。请在 frontend 运行 npm install 和 npm run build。'}, status_code=503)

    return app


app = create_app()
