"""Lossless material reading, resumable checkpoints and bounded overview reduction.

Only source batches carry original text. Overview summaries retain real source
references; teaching and review fetch those originals again instead of teaching
from the summary. Checkpoints belong to one immutable import, never a global KB.
"""
import copy
import hashlib
import json

from .model_client import MAX_CHAPTER_REQUEST_BYTES, encode_chat_request, redact
from .prompts import load_prompt


ROLE_IDS = {'mage', 'paladin', 'druid', 'shaman', 'priest'}
MAX_SUMMARY_BYTES = 7000
MAX_REDUCTION_BYTES = 18000


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def source_version(record):
    return digest([record['snapshot_hash'], record.get('scope_revision', 0), record.get('scope')])


def complete_analysis(record, batches):
    if record.get('status') != 'analyzed':
        return False
    # A prior one-request result is still complete only if all text fits one batch.
    return record.get('analysis_complete', len(batches) == 1) is True


def _text(value, maximum, label):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f'材料分析的{label}格式或长度无效。')
    return value.strip()


def validate_analysis(value, allowed_ids, *, require_outline):
    if not isinstance(value, dict):
        raise ValueError('材料分析结果必须是对象。')
    result = {'summary': _text(value.get('summary'), 1200, '摘要'),
              'content_type': _text(value.get('content_type'), 150, '材料类型')}
    recommendations = value.get('recommendations')
    if not isinstance(recommendations, list) or not 1 <= len(recommendations) <= 3:
        raise ValueError('材料分析必须给出一至三个有依据的职业建议。')
    result['recommendations'] = []
    for recommendation in recommendations:
        if not isinstance(recommendation, dict) or recommendation.get('role') not in ROLE_IDS:
            raise ValueError('材料分析的职业建议无效。')
        result['recommendations'].append({'role': recommendation['role'],
            'reason': _text(recommendation.get('reason'), 500, '适配理由'),
            'focus': _text(recommendation.get('focus'), 500, '学习重点')})
    if len({r['role'] for r in result['recommendations']}) != len(result['recommendations']):
        raise ValueError('材料分析的职业建议重复。')
    outline = value.get('outline', [])
    if not isinstance(outline, list) or len(outline) > 8 or (require_outline and not outline):
        raise ValueError('分批分析缺少可定位的知识概览。')
    result['outline'] = []
    for section in outline:
        if not isinstance(section, dict):
            raise ValueError('知识概览格式无效。')
        ids = section.get('source_ids')
        if not isinstance(ids, list) or not ids or len(ids) > 32 or any(not isinstance(sid, str) or sid not in allowed_ids for sid in ids):
            raise ValueError('知识概览引用了未提供的原文。')
        result['outline'].append({'title': _text(section.get('title'), 120, '主题标题'),
            'summary': _text(section.get('summary'), 800, '主题说明'), 'source_ids': list(dict.fromkeys(ids))})
    if len(json.dumps(result, ensure_ascii=False).encode()) > MAX_SUMMARY_BYTES:
        raise ValueError('材料摘要超过汇总预算，已保留其他完成批次，请继续分析重试本批。')
    return result


class StaleAnalysis(ValueError):
    pass


class MaterialAnalyzer:
    def __init__(self, store, sources, ask, identity, model_name):
        self.store, self.sources, self.ask = store, sources, ask
        self.identity, self.model_name = identity, model_name

    def _fits(self, prompt, payload):
        user = redact(json.dumps(payload, ensure_ascii=False))
        return len(encode_chat_request(self.model_name(), prompt, user,
            response_format={'type': 'json_object'})) <= MAX_CHAPTER_REQUEST_BYTES

    def _groups(self, nodes, prompt, focus):
        groups, current = [], []
        for node in nodes:
            candidate = current + [node]
            payload = {'analyses': [n['result'] for n in candidate], 'focus': focus, 'summary_budget_bytes': 3000}
            if current and (len(json.dumps(payload, ensure_ascii=False).encode()) > MAX_REDUCTION_BYTES or not self._fits(prompt, payload)):
                groups.append(current)
                current = []
            current.append(node)
            single = {'analyses': [n['result'] for n in current], 'focus': focus, 'summary_budget_bytes': 3000}
            if not self._fits(prompt, single):
                raise ValueError('汇总提示词与单个摘要超过请求预算，请检查提示词或关注重点；原文分析进度已保留。')
        if current:
            groups.append(current)
        return groups

    def run(self, iid, focus=None):
        record = self.sources.get(iid)
        focus = record.get('analysis_focus', '') if focus is None else focus
        if not isinstance(focus, str) or len(focus) > 2000:
            raise ValueError('关注重点不能超过 2,000 字符。')
        prompts = {name: load_prompt(name) for name in
            ('adventure_analysis', 'adventure_analysis_batch', 'adventure_analysis_merge')}
        # JSON is encoded once inside the chat envelope as well. Quotes and
        # backslashes can therefore overflow a raw-source byte budget; size the
        # actual request and repartition the original text before any model call.
        budget = 16000
        while True:
            batches = self.sources.batches(iid, max_bytes=budget)
            if not batches:
                raise ValueError('所选范围没有可读取的文字。')
            prompt = prompts['adventure_analysis'] if len(batches) == 1 else prompts['adventure_analysis_batch']
            if all(self._fits(prompt, {'sources': b['sources'], 'focus': focus}) for b in batches):
                break
            if budget <= 512:
                raise ValueError('分析提示词或模型名称过长，最小原文批次仍超过请求预算，请检查提示词和模型设置。')
            budget = max(512, budget // 2)
        identity, version = self.identity(), source_version(record)
        fingerprint = digest([1, identity, version, focus, prompts,
                              [[batch['id'], digest(batch['sources'])] for batch in batches]])
        saved = record.get('analysis_work', {})
        work = copy.deepcopy(saved) if saved.get('fingerprint') == fingerprint else {
            'fingerprint': fingerprint, 'source_version': version, 'batches': {}, 'reductions': {}}
        total_chars = sum(batch['chars'] for batch in batches)

        def current():
            value = self.sources.get(iid)
            if source_version(value) != version or self.identity() != identity:
                raise StaleAnalysis('材料范围或模型设置已变化，本次未发布分析，请继续分析新的范围。')
            return value

        def save(phase, current_batch=None, *, result=None, error=None):
            with self.store.lock:
                value = current()
                completed = [b for b in batches if work['batches'].get(b['id'], {}).get('status') == 'completed']
                failed = sum(work['batches'].get(b['id'], {}).get('status') == 'failed' for b in batches)
                progress = {'phase': phase, 'total_batches': len(batches), 'completed_batches': len(completed),
                    'failed_batches': failed, 'processed_chars': sum(b['chars'] for b in completed), 'total_chars': total_chars}
                if current_batch is not None:
                    progress['current_batch'] = current_batch
                value.update(analysis_work=copy.deepcopy(work), analysis_progress=progress, analysis_focus=focus,
                             analysis_complete=phase == 'complete', status='analyzed' if phase == 'complete' else 'failed' if phase == 'failed' else 'analyzing')
                value.pop('analysis', None)
                value.pop('error', None)
                if result is not None:
                    value['analysis'] = copy.deepcopy(result)
                if error:
                    value['error'] = redact(str(error))
                self.store.put('imports_v2', iid, value)

        try:
            save('reading')
            for index, batch in enumerate(batches, 1):
                if work['batches'].get(batch['id'], {}).get('status') == 'completed':
                    continue
                save('reading', index)
                try:
                    prompt = prompts['adventure_analysis'] if len(batches) == 1 else prompts['adventure_analysis_batch']
                    result = self.ask(prompt, {'sources': batch['sources'], 'focus': focus})
                    current()
                    result = validate_analysis(result, {s['id'] for s in batch['sources']}, require_outline=len(batches) > 1)
                    work['batches'][batch['id']] = {'status': 'completed', 'result': result}
                except StaleAnalysis:
                    raise
                except Exception as error:
                    current()
                    work['batches'][batch['id']] = {'status': 'failed', 'error': redact(str(error))}
                save('reading')
            failures = [batch for batch in batches if work['batches'].get(batch['id'], {}).get('status') != 'completed']
            if failures:
                first_error = work['batches'][failures[0]['id']].get('error', '批次未完成。')
                save('failed', error=f'{len(failures)} 个批次未完成，已保留其他完成批次。继续分析将重试未完成部分。{first_error}')
                return
            nodes = [{'id': b['id'], 'result': work['batches'][b['id']]['result']} for b in batches]
            level = 0
            while len(nodes) > 1:
                save('summarizing')
                next_nodes = []
                groups = self._groups(nodes, prompts['adventure_analysis_merge'], focus)
                compress = len(groups) == len(nodes)
                for group in groups:
                    if len(group) == 1 and not compress:
                        next_nodes.append(group[0])
                        continue
                    key = digest([level, group, compress])
                    checkpoint = work['reductions'].get(key, {})
                    if checkpoint.get('status') == 'completed':
                        result = checkpoint['result']
                    else:
                        try:
                            payload = {'analyses': [n['result'] for n in group], 'focus': focus}
                            if compress:
                                payload['summary_budget_bytes'] = 3000
                            result = self.ask(prompts['adventure_analysis_merge'], payload)
                            current()
                            allowed = {sid for node in group for section in node['result']['outline'] for sid in section['source_ids']}
                            result = validate_analysis(result, allowed, require_outline=True)
                            if compress and len(json.dumps(result, ensure_ascii=False).encode()) > 3000:
                                raise ValueError('摘要压缩未达到合并预算，请继续分析重试汇总；原文进度已保留。')
                            work['reductions'][key] = {'status': 'completed', 'result': result}
                        except StaleAnalysis:
                            raise
                        except Exception as error:
                            current()
                            work['reductions'][key] = {'status': 'failed', 'error': redact(str(error))}
                            save('failed', error='原文批次已读取，材料概览汇总未完成；继续分析将从汇总处重试。' + redact(str(error)))
                            return
                        save('summarizing')
                    next_nodes.append({'id': key, 'result': result})
                nodes = next_nodes
                level += 1
                if compress and len(self._groups(nodes, prompts['adventure_analysis_merge'], focus)) == len(nodes):
                    raise ValueError('汇总提示词过长，压缩摘要后仍无法合并，请检查提示词；原文进度已保留。')
            save('complete', result=nodes[0]['result'])
        except StaleAnalysis as error:
            # Never overwrite a new scope/job with an older in-flight snapshot.
            with self.store.lock:
                value = self.sources.get(iid)
                if value.get('analysis_work', {}).get('fingerprint') == fingerprint:
                    value.update(status='failed', analysis_complete=False, error=redact(str(error)))
                    value.pop('analysis', None)
                    if value.get('analysis_progress'):
                        value['analysis_progress']['phase'] = 'failed'
                    self.store.put('imports_v2', iid, value)
        except Exception as error:
            save('failed', error=error)

    def planning_context(self, iid):
        record = self.sources.get(iid)
        batches = self.sources.batches(iid)
        if len(batches) == 1 and not record.get('analysis_work'):
            return batches[0]['sources']
        if not complete_analysis(record, batches):
            raise ValueError('请先完成材料的完整分批分析。')
        # Source IDs in this compact overview are aliases for exact originals,
        # not a replacement for original material in teaching or evaluation.
        sections = record.get('analysis', {}).get('outline', [])
        if not sections:
            if len(batches) == 1:
                return batches[0]['sources']
            raise ValueError('材料概览缺少原文索引，请重新分析。')
        result = []
        for section in sections:
            originals = self.sources.context_for_ids(iid, section['source_ids'])
            original = originals[0]
            result.append({key: value for key, value in original.items() if key != 'content'} |
                          {'content': f"AI 分批阅读概览（规划用）：{section['title']}：{section['summary']}",
                           'related_source_ids': section['source_ids']})
        return result
