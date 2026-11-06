"""Explicit synthetic browser QA server, never imported by the production app.

Run with CODEWARDEN_DATA pointing at a disposable directory. The fixed model
responses below exercise UI wiring, not real model teaching quality.
"""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if not os.environ.get('CODEWARDEN_DATA'):
    raise RuntimeError('Browser fixture requires an isolated CODEWARDEN_DATA directory.')

from backend.api import create_app


class FixtureModel:
    def public(self):
        return {'base_url': 'http://127.0.0.1:9/v1', 'model': 'synthetic-browser-fixture', 'key_set': False, 'generation_timeout': 30}

    def ask_json(self, system, user, **kwargs):
        payload = json.loads(user)
        sources = payload.get('sources', [])
        refs = [source['id'] for source in sources]
        if 'ANALYSIS' in system:
            history = any('历史' in source.get('content', '') for source in sources)
            role = 'priest' if history else 'mage'
            return {'summary': '合成联调材料：学习哨站的值守记录与交接流程。', 'content_type': '历史资料' if history else '操作文档', 'recommendations': [{'role': role, 'reason': '材料包含需要理解的概念、步骤和背景。', 'focus': '理解材料中的关系与操作顺序'}]}
        if 'PLAN' in system:
            return {'title': '第八哨站的值守簿', 'summary': '合成联调冒险：了解两项独立知识，再完成营地交接。', 'topics': [{'id': 'records', 'title': '点亮记录室', 'description': '理解值守记录为什么需要持续补充。'}, {'id': 'handover', 'title': '恢复换班钟', 'description': '把交接步骤整理成接班人能理解的过程。'}], 'objectives': [{'id': 'records', 'topic_id': 'records', 'title': '理解记录的作用', 'description': '用自己的话说明记录帮助谁完成什么任务。', 'source_ids': refs[:1], 'prerequisite_ids': [], 'required': True}, {'id': 'handover', 'topic_id': 'handover', 'title': '理解交接顺序', 'description': '解释交接步骤之间的联系。', 'source_ids': refs[:1], 'prerequisite_ids': [], 'required': True}]}
        if 'TASK' in system:
            obj = payload['objective']
            return {'title': obj['title'], 'brief': '导师把值守簿推到灯下。你需要先理解其中的安排，再向接班人解释。', 'objective': obj['description'], 'teaching': [{'title': '先读一段记录', 'body': '记录保留观察结果，交接让下一位值守者知道当前状态和待办事项。它们共同帮助工作连续进行。'}], 'question': '请说明记录和交接之间的关系。', 'interaction': 'explain', 'options': [], 'source_refs': refs[:1], 'criteria': ['说明记录保存的信息', '解释这些信息怎样帮助交接'], 'hints': ['想一想接班人到来时还不知道什么。', '把保存信息与继续行动联系起来。'], 'answer_baseline': '记录保存状态和待办，接班人据此继续工作。', 'run_entry': None}
        task = payload['task']
        passed = len(payload.get('answer', '')) >= 8
        return {'status': 'passed' if passed else 'needs_work', 'feedback': '合成联调评审：回答已保存。' if passed else '请再说明信息如何帮助下一位值守者。', 'checks': [{'criterion': criterion, 'passed': passed, 'evidence': '合成测试响应，仅验证界面与存储流程。'} for criterion in task['criteria']]}


app = create_app(use_keyring=False)
app.state.adventures.model = FixtureModel()

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8876, log_level='warning')
