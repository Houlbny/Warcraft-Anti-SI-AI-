"""Durable private generation segments. No partial draft is a playable chapter."""
import copy
import hashlib
import json
from datetime import datetime, timezone

from .model_client import redact, MAX_CHAPTER_REQUEST_BYTES
from .generation_prompts import SYSTEMS
from .generation_validation import ContentValidationError

RULES_VERSION = 'segmented-chapter-1'
MAX_USER_BYTES = 24000
MAX_REQUEST_BYTES = MAX_CHAPTER_REQUEST_BYTES
# Includes JSON escaping of bounded diagnostic metadata in the outer request.
FEEDBACK_RESERVE = 1024
JSON_SUFFIX = '\n只返回 JSON，不要 Markdown。'


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def compact(value, maximum):
    text = str(value)
    raw = text.encode('utf-8')
    return text if len(raw) <= maximum else raw[:maximum - 3].decode('utf-8', errors='ignore') + '…'


def wire_size(model_name, system, user):
    return len(json.dumps({'model': model_name, 'messages': [
        {'role': 'system', 'content': redact(system + JSON_SUFFIX)},
        {'role': 'user', 'content': redact(user)}], 'stream': False},
        ensure_ascii=False, separators=(',', ':')).encode())


def validate_analysis(response, sources):
    from .campaigns import text_field
    if not isinstance(response, dict):
        raise ValueError('源码分析未返回有效对象，已保存部分未改变。')
    summary = text_field(response, 'summary', maximum=400)
    entries = response.get('files')
    expected = {s['path'] for s in sources}
    if not isinstance(entries, list) or len(entries) != len(expected):
        raise ValueError('源码分析的文件范围不完整。')
    files = []
    for index, item in enumerate(entries):
        if not isinstance(item, dict) or not isinstance(item.get('path'), str) or item['path'] not in expected:
            raise ValueError('源码分析引用了未提供的文件。')
        files.append({'path': item['path'], 'summary': text_field(item, 'summary', maximum=240, path=f'files[{index}].summary')})
    if {f['path'] for f in files} != expected:
        raise ValueError('源码分析的文件存在重复或缺失。')
    return {'summary': summary, 'files': files}


def validate_outline(response, role, sources, service):
    from .campaigns import text_field, supported_entry
    if not isinstance(response, dict):
        raise ValueError('章节大纲未返回对象。')
    title = text_field(response, 'title', maximum=120)
    summary = text_field(response, 'summary', maximum=1200)
    entries = response.get('quests')
    if not isinstance(entries, list) or not 6 <= len(entries) <= 10:
        raise ValueError('章节大纲需要6–10关，未发布不完整章节。')
    allowed = {s['path']: s for s in sources}
    quests = []
    for i, item in enumerate(entries):
        if not isinstance(item, dict):
            raise ValueError('章节大纲包含无效关卡。')
        kind, mode, focus = item.get('type'), item.get('verification_mode'), item.get('focus')
        if kind not in {'explore', 'predict', 'repair', 'feature'} or mode not in {'answer', 'explain', 'run', 'review'} or focus not in {'language', 'structure', 'flow', 'feature', 'regression'}:
            raise ValueError('章节大纲的类型、方向或验收方式无效。')
        if (kind in {'repair', 'feature'}) != (mode in {'run', 'review'}):
            raise ValueError('章节大纲的任务与验收方式不匹配。')
        path, line = item.get('source_file'), item.get('line')
        if path not in allowed or type(line) is not int or not 1 <= line <= allowed[path]['line_count']:
            raise ValueError('章节大纲引用了未提供的源码或无效行号。')
        dependencies = item.get('depends_on')
        if not isinstance(dependencies, list) or len(dependencies) > 3 or any(type(n) is not int or not 1 <= n <= i for n in dependencies) or len(set(dependencies)) != len(dependencies):
            raise ValueError('章节大纲必须只依赖前关，最多三个直接依赖。')
        quest = {'title': text_field(item, 'title', maximum=120, path=f'quests[{i}].title'), 'objective': text_field(item, 'objective', maximum=300, path=f'quests[{i}].objective'),
                 'type': kind, 'focus': focus, 'source_file': path, 'line': line, 'verification_mode': mode,
                 'depends_on': dependencies, 'expected_changes': text_field(item, 'expected_changes', maximum=300, path=f'quests[{i}].expected_changes')}
        if kind in {'repair', 'feature'}:
            entry = item.get('run_entry') or path
            if entry not in allowed or not supported_entry(entry):
                raise ValueError('章节大纲没有受支持的真实运行入口。')
            quest['run_entry'] = entry
        quests.append(quest)
    service.validate_role(quests, role)
    return {'title': title, 'summary': summary, 'quests': quests}


def excerpt(source, line, maximum=2200):
    """Preserve complete source lines surrounding the actual numbered reference."""
    lines = source['content'].splitlines(keepends=True)
    anchor = max(0, min(line - 1, len(lines) - 1))
    start, end = max(0, anchor - 12), min(len(lines), anchor + 25)
    while len(''.join(lines[start:end]).encode()) > maximum and end - start > 1:
        if anchor - start >= end - anchor - 1:
            start += 1
        else:
            end -= 1
    content = ''.join(lines[start:end])
    if len(content.encode()) > maximum:
        raise ValueError('引用源码的单行过长，请缩小练习源码后重新生成。')
    return {'path': source['path'], 'language': source['language'], 'content': content,
            'start_line': start + 1, 'reference_line': line, 'line_count': source['line_count'],
            'symbols': [s for s in source['symbols'] if start < s['line'] <= end]}


def validate_check(response):
    if not isinstance(response, dict):
        raise ContentValidationError('response', 'type', actual_type=type(response).__name__, expected_type='dict')
    if type(response.get('consistent')) is not bool:
        raise ContentValidationError('consistent', 'type', actual_type=type(response.get('consistent')).__name__, expected_type='bool')
    issues = response.get('issues')
    if not isinstance(issues, list):
        raise ContentValidationError('issues', 'type', actual_type=type(issues).__name__, expected_type='list')
    if len(issues) > 10 or any(not isinstance(issue, (str, dict)) for issue in issues):
        raise ContentValidationError('issues', 'invalid_content', actual_length=len(issues), maximum=10)
    # A valid conflict is a semantic finding, not an invitation to retry until true.
    return response


class SegmentedGeneration:
    def __init__(self, service, project_id, role, progress):
        self.service, self.project_id, self.role, self.progress = service, project_id, role, progress
        self.request_attempt, self.validation_error = 1, None
        self.project = service.projects.get(project_id)
        self.version = service.lessons.workspace_version(project_id)
        self.files, self.sources = service.context(project_id)
        if self.version != service.lessons.workspace_version(project_id):
            raise ValueError('读取源码期间练习文件已变化，未保存旧源码分析；请明确继续。')
        if not self.sources:
            raise ValueError('项目中没有可用于关卡的文本源码。')
        settings = service.model.public()
        self.identity = {'role': role, 'base_url': settings['base_url'], 'model': settings['model'], 'rules': RULES_VERSION}
        # A single bounded source per analysis request avoids oversized metadata
        # forcing users to restart the same un-sendable two-file request.
        self.batches = [[source] for source in self.sources]
        old = service.store.get('campaign_drafts', project_id, {})
        compatible = old.get('identity') == self.identity
        unchanged = compatible and old.get('workspace_version') == self.version
        self.draft = old if unchanged else {
            'identity': self.identity, 'workspace_version': self.version,
            'analyses': old.get('analyses', {}) if compatible else {}, 'outline': None, 'details': {}, 'checked': False}
        self.batch_keys = [fingerprint([(s['path'], s['hash']) for s in batch]) for batch in self.batches]
        self.draft['analyses'] = {key: value for key, value in self.draft.get('analyses', {}).items() if key in self.batch_keys}
        self.reused = len(self.draft['analyses'])
        self.note = '源码或生成配置已变化，旧大纲和关卡草稿已作废。' if old and not unchanged else ''
        if self.reused:
            self.note += f'已复用{self.reused}批未变化源码的已保存分析。'
        self.save()

    def identity_now(self):
        settings = self.service.model.public()
        return {'role': self.role, 'base_url': settings['base_url'], 'model': settings['model'], 'rules': RULES_VERSION}

    def guard(self):
        if self.identity_now() != self.identity or self.service.lessons.workspace_version(self.project_id) != self.version:
            raise ValueError('生成期间源码或模型配置已变化，当前响应未保存；请明确继续以重新规划。')

    def save(self, diagnostic=None):
        metadata = self.progress.get('generation', {})
        self.progress['generation'] = self.metadata(metadata.get('stage', 'analyzing'),
            metadata.get('current_quest'), metadata.get('message', ''))
        # Counts and the accepted segment share a transaction: a crash cannot
        # leave the durable draft ahead of the public resumable progress.
        records = [('campaign_drafts', self.project_id, self.draft), ('campaigns', self.project_id, self.progress)]
        if diagnostic is not None:
            recent = self.service.store.get('generation_diagnostics', self.project_id, [])
            records.append(('generation_diagnostics', self.project_id, [diagnostic] + recent[:19]))
        self.service.store.put_many(records)

    def metadata(self, stage, current=None, message=''):
        outline = self.draft.get('outline')
        quests = outline['quests'] if outline else []
        details = self.draft.get('details', {})
        return {'stage': stage, 'analysis_completed': len(self.draft['analyses']), 'analysis_total': len(self.batches),
                'quests_completed': len(details), 'quests_total': len(quests), 'current_quest': current,
                'quest_titles': [{'title': q['title'], 'status': 'saved' if str(i + 1) in details else 'generating' if current == i + 1 else 'pending'} for i, q in enumerate(quests)],
                'resumable': True, 'message': message or self.note or '仅在整章校验通过后开放关卡。',
                'attempt': self.request_attempt, 'max_attempts': 2, 'validation_error': self.validation_error}

    def update(self, stage, current=None, message='', phase='preparing'):
        self.progress.update({'phase': phase, 'generation': self.metadata(stage, current, message), 'source_count': len(self.sources)})
        self.service.store.put('campaigns', self.project_id, self.progress)

    def ask(self, stage, payload, current=None):
        from .campaigns import ROLES
        self.guard()
        reserve = 0 if payload.get('validation_feedback') else FEEDBACK_RESERVE
        for level in range(4):
            user = json.dumps({'stage': stage, 'role': ROLES[self.role], **payload}, ensure_ascii=False, separators=(',', ':'))
            size = wire_size(self.identity['model'], SYSTEMS[stage], user)
            if len(user.encode()) <= MAX_USER_BYTES - reserve and size <= MAX_REQUEST_BYTES - reserve:
                break
            if stage == 'planning':
                for source in payload['file_index']:
                    source['symbols'] = source.get('symbols', [])[:2] if level == 0 else []
                for analysis in payload['analyses']:
                    analysis['summary'] = compact(analysis['summary'], 80 if level == 0 else 40)
                    for file in analysis['files']:
                        file['summary'] = compact(file['summary'], 120 if level == 0 else 60)
                        if level >= 1 and 'path' in file:
                            file['file_index'] = next(i + 1 for i, item in enumerate(payload['file_index']) if item['path'] == file['path'])
                            del file['path']
            elif stage == 'quests':
                for prerequisite in payload['prerequisites']:
                    for key, maximum in [('objective', 300), ('verification', 400), ('planned_changes', 300)]:
                        prerequisite[key] = compact(prerequisite[key], maximum)
                originals = {source['path']: source for source in self.sources}
                payload['sources'] = [excerpt(originals[source['path']], source['reference_line'], 1200 if level == 0 else 700) for source in payload['sources']]
                payload['revision_notes'] = [compact(note, 180) for note in payload.get('revision_notes', [])[:6]]
            elif stage == 'checking':
                # Final consistency must retain full objectives and acceptance
                # conditions. The caller partitions over-budget checks instead.
                raise ValueError('一致性分组仍超过请求上限，未发送请求。')
            else:
                for source in payload.get('sources', []):
                    source['symbols'] = []
        else:
            raise ValueError('当前分段元数据仍超过UTF-8大小上限，未发送请求。请检查过长文件路径或异常模型字段。')
        self.progress['input_bytes'] = size
        message = ('正在自动纠正重试 1/1：' + self.validation_error['message']) if self.request_attempt == 2 and self.validation_error else ''
        self.update(stage, current, message=message, phase='requesting')
        result = self.service.model.ask_json(SYSTEMS[stage], user, purpose='chapter')
        self.guard()
        self.update(stage, current, message=message, phase='validating')
        return result

    def validated(self, stage, payload, validator, current=None, group=None):
        """Only invalid response content gets one corrective request, never I/O errors."""
        segment = fingerprint({'stage': stage, 'identity': self.identity, 'version': self.version, 'payload': payload})
        failures = self.draft.setdefault('format_failures', {})
        feedback = failures.get(segment)
        label = {'analyzing': f'源码分析第{payload.get("batch")}/{len(self.batches)}批',
                 'planning': '章节大纲', 'quests': f'第{current}关细化', 'checking': f'整章检查第{group}组'}[stage]
        for attempt in (1, 2):
            self.guard()
            self.request_attempt, self.validation_error = attempt, feedback
            request = copy.deepcopy(payload)
            if feedback:
                request['validation_feedback'] = feedback
            try:
                # ask's guard/budget/network errors are ordinary ValueError and
                # intentionally escape this dedicated content-error catch.
                response = self.ask(stage, request, current)
            except ContentValidationError as error:
                failure = error
                constraint = None
            else:
                try:
                    accepted = validator(response)
                except ContentValidationError as error:
                    failure, constraint = error, None
                except ValueError as error:
                    # Only these local validators are inside this block; their
                    # fixed constraint messages never contain model field values.
                    failure = ContentValidationError('response', 'invalid_content')
                    constraint = str(error)
                except (TypeError, KeyError):
                    failure, constraint = ContentValidationError('response', 'invalid_content'), None
                else:
                    self.guard()
                    failures.pop(segment, None)
                    self.validation_error = None
                    # Caller commits this removal together with the accepted segment.
                    return accepted
            self.guard()
            feedback = {**failure.to_dict(), 'message': compact(constraint or str(failure), 360)}
            failures[segment] = feedback
            self.validation_error = feedback
            self.progress.update(phase='validating')
            self.progress['generation'] = self.metadata(stage, current, label + '：' + feedback['message'])
            self.save({'stage': stage, 'segment': segment, 'batch': payload.get('batch'), 'quest_number': current,
                       'check_group': group, 'attempt': attempt, 'error': feedback,
                       'occurred_at': datetime.now(timezone.utc).isoformat()})
            if attempt == 2:
                raise ValueError(f'{label}未通过（已自动纠正1次）：{feedback["message"]} 已保存进度保留，可继续未完成部分。') from None

    def validate_detail(self, response, planned):
        from .campaigns import text_field
        item = response.get('quest') if isinstance(response, dict) else None
        if not isinstance(item, dict):
            raise ContentValidationError('quest', 'type', actual_type=type(item).__name__, expected_type='dict')
        for field in ('title', 'type', 'focus', 'source_file', 'line', 'verification_mode'):
            if item.get(field) != planned[field]:
                raise ValueError('关卡细化偏离已保存大纲，必须保留标题、类型、方向、源码位置与验收模式。')
        if planned.get('run_entry') and (item.get('run_entry') or item.get('source_file')) != planned['run_entry']:
            raise ValueError('关卡细化改变了已保存大纲的运行入口。')
        text_field(item, 'objective', maximum=300, path='quest.objective')
        text_field(item, 'verification', maximum=400, path='quest.verification')
        if item['verification_mode'] == 'run':
            text_field(item, 'expected_output', maximum=400, path='quest.expected_output')
        return self.service.validate([item], self.role, self.sources, partial=True)[0]

    def check_groups(self, outline, checks):
        from .campaigns import ROLES
        chapter = {key: outline[key] for key in ('title', 'summary')}
        payload = {'chapter': chapter, 'quests': checks}
        user = json.dumps({'stage': 'checking', 'role': ROLES[self.role], **payload}, ensure_ascii=False, separators=(',', ':'))
        if (len(user.encode()) <= MAX_USER_BYTES - FEEDBACK_RESERVE
                and wire_size(self.identity['model'], SYSTEMS['checking'], user) <= MAX_REQUEST_BYTES - FEEDBACK_RESERVE):
            groups = [payload]
        else:
            # Preserve every full task and test adjacent transitions plus each
            # transitive dependency pair. Role coverage is already checked locally.
            pairs = {(i, i + 1) for i in range(len(checks) - 1)}
            for index, planned in enumerate(outline['quests']):
                pending = list(planned['depends_on']); ancestors = set()
                while pending:
                    ancestor = pending.pop()
                    if ancestor not in ancestors:
                        ancestors.add(ancestor); pending.extend(outline['quests'][ancestor - 1]['depends_on'])
                pairs.update((ancestor - 1, index) for ancestor in ancestors)
            groups = [{'chapter': chapter, 'quests': [checks[a], checks[b]], 'scope': 'dependency_pair'} for a, b in sorted(pairs)]
        saved = self.draft.setdefault('check_groups', [])
        for group_number, payload in enumerate(groups, 1):
            key = fingerprint(payload)
            if key in saved:
                continue
            response = self.validated('checking', payload, validate_check, group=group_number)
            if not response['consistent'] or response['issues']:
                return response
            self.guard(); saved.append(key); self.save()
        return {'consistent': True, 'issues': []}

    def run(self):
        from .campaigns import text_field
        for number, (key, sources) in enumerate(zip(self.batch_keys, self.batches), 1):
            if key in self.draft['analyses']:
                continue
            public_sources = [{**{k: v for k, v in s.items() if k not in {'hash', 'symbols'}},
                'symbols': [{**symbol, 'name': compact(symbol['name'], 80)} for symbol in s['symbols'][:8]]} for s in sources]
            analysis = self.validated('analyzing', {'project': self.project['name'], 'batch': number, 'sources': public_sources},
                                      lambda response: validate_analysis(response, sources))
            self.guard(); self.draft['analyses'][key] = analysis; self.save(); self.update('analyzing')
        if not self.draft.get('outline'):
            analyses = [{'summary': compact(self.draft['analyses'][key]['summary'], 160),
                'files': [{**file, 'summary': compact(file['summary'], 200)} for file in self.draft['analyses'][key]['files']]}
                for key in self.batch_keys]
            index = [{**{k: s[k] for k in ('path', 'language', 'line_count')},
                'symbols': [{**symbol, 'name': compact(symbol['name'], 60)} for symbol in s['symbols'][:4]]} for s in self.sources]
            outline = self.validated('planning', {'project': self.project['name'], 'languages': self.project['languages'],
                'analyses': analyses, 'file_index': index, 'revision_notes': self.draft.get('outline_notes', [])},
                lambda response: validate_outline(response, self.role, self.sources, self.service))
            self.guard(); self.draft['outline'] = outline; self.save(); self.update('planning')
        outline = self.draft['outline']
        allowed = {s['path']: s for s in self.sources}
        for index, planned in enumerate(outline['quests'], 1):
            if str(index) in self.draft['details']:
                continue
            prerequisites = []
            refs = {planned['source_file']: planned['line']}
            if planned.get('run_entry'):
                refs.setdefault(planned['run_entry'], 1)
            for dependency in planned['depends_on']:
                saved = self.draft['details'][str(dependency)]
                earlier = outline['quests'][dependency - 1]
                prerequisites.append({'quest_number': dependency, 'title': saved['title'], 'objective': saved['objective'],
                    'verification': saved['verification'], 'planned_changes': earlier['expected_changes']})
                refs.setdefault(saved['source_file'], saved['line'])
            # A reading quest may depend on an earlier code change indirectly.
            # Carry those accepted constraints without copying all long rubrics.
            ancestors = set(planned['depends_on'])
            pending = list(ancestors)
            while pending:
                for ancestor in outline['quests'][pending.pop() - 1]['depends_on']:
                    if ancestor not in ancestors:
                        ancestors.add(ancestor); pending.append(ancestor)
            for dependency in sorted(ancestors - set(planned['depends_on'])):
                saved = self.draft['details'][str(dependency)]
                if saved['type'] not in {'repair', 'feature'}:
                    continue
                earlier = outline['quests'][dependency - 1]
                prerequisites.append({'quest_number': dependency, 'title': saved['title'],
                    'objective': compact(saved['objective'], 300), 'verification': compact(saved['verification'], 400),
                    'planned_changes': compact(earlier['expected_changes'], 300), 'transitive': True})
            saved = self.validated('quests', {'chapter': {k: outline[k] for k in ('title', 'summary')},
                'quest_number': index, 'outline': planned, 'prerequisites': prerequisites,
                'sources': [excerpt(allowed[path], line) for path, line in refs.items()],
                'revision_notes': self.draft.get('revision_notes', {}).get(str(index), [])},
                lambda response: self.validate_detail(response, planned), current=index)
            self.guard(); self.draft['details'][str(index)] = saved; self.save(); self.update('quests')
        tasks = self.service.validate([self.draft['details'][str(i + 1)] for i in range(len(outline['quests']))], self.role, self.sources)
        if not self.draft.get('checked'):
            checks = []
            for i, task in enumerate(tasks):
                planned = outline['quests'][i]
                checks.append({**{key: task.get(key) for key in ('title', 'type', 'focus', 'objective', 'source_file', 'line', 'run_entry', 'verification_mode', 'verification')},
                    'quest_number': i + 1, 'depends_on': planned['depends_on'], 'planned_changes': planned['expected_changes']})
                if task.get('expected_output') is not None:
                    checks[-1]['expected_output'] = task['expected_output']
            response = self.check_groups(outline, checks)
            if not isinstance(response, dict) or type(response.get('consistent')) is not bool or not isinstance(response.get('issues'), list):
                raise ValueError('整章一致性检查响应无效，已保存关卡保留，明确继续时重新检查。')
            issues = response['issues']
            if len(issues) > 10 or any(not isinstance(issue, (str, dict)) for issue in issues):
                raise ValueError('整章一致性检查的问题列表无效，已保存关卡保留。')
            if not response['consistent'] or issues:
                affected = {issue['quest_number'] for issue in issues if isinstance(issue, dict) and type(issue.get('quest_number')) is int and 1 <= issue['quest_number'] <= len(tasks)}
                if not affected:
                    affected = set(range(1, len(tasks) + 1))
                for i, planned in enumerate(outline['quests'], 1):
                    if any(n in affected for n in planned['depends_on']):
                        affected.add(i)
                notes = [compact(issue.get('message', '') if isinstance(issue, dict) else issue, 240)
                         for issue in issues if isinstance(issue, str) or isinstance(issue.get('message'), str)]
                notes = [note for note in notes if note.strip()] or ['重新核对前置改动、后关目标和运行验收之间的冲突。']
                self.draft['consistency_failures'] = self.draft.get('consistency_failures', 0) + 1
                replan = any(isinstance(issue, dict) and issue.get('scope') == 'outline' for issue in issues)
                # A repeated semantic failure may reveal an outline constraint
                # which details cannot satisfy. Explicit resume then replans once
                # from saved analyses, with the accepted checker feedback.
                replan = replan or self.draft['consistency_failures'] >= 2
                if replan:
                    self.draft.update(outline=None, details={}, outline_notes=notes, consistency_failures=0)
                else:
                    revision = self.draft.setdefault('revision_notes', {})
                    for i in affected:
                        self.draft['details'].pop(str(i), None)
                        revision[str(i)] = notes
                self.draft['check_groups'] = []
                self.draft['checked'] = False; self.guard(); self.save()
                self.update('checking', message='整章大纲存在冲突，已保留源码分析；明确继续后携带反馈重新规划。' if replan else f'整章一致性未通过，已保留分析与大纲；明确继续后携带反馈重新细化{len(affected)}个受影响关卡。')
                raise ValueError('整章一致性未通过，未发布关卡；可明确继续重新细化受影响部分。')
            self.guard(); self.draft['checked'] = True; self.save()
        self.guard()
        return outline, tasks
