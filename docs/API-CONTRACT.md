# v0.2 API and Python interfaces

All JSON. API prefix `/api`. Error: `{detail: string}`. Language IDs: `java`, `python`, `javascript`, `typescript`, `go`, `shell`.

The current frontend flow is import source → choose `mage` / `paladin` / `druid` → generate a complete AI chapter → complete quests in order. Mage emphasizes language knowledge and guidance, paladin structure and core implementation, and druid broader understanding and extension. New chapters contain 6–10 quests, each with a source reference, objective, 2–4 steps and declared verification mode. Generation requires a configured model; loopback model services may omit an API key. Failed generation can be retried with the selected role; saved chapters and progress persist across service restarts. Browsing an existing chapter does not regenerate it.

The new campaign state, role selection, sequential unlocking and AI review endpoints are specified in [CAMPAIGN-CONTRACT.md](CAMPAIGN-CONTRACT.md). It extends the base contracts below. Only the first incomplete quest is available; completed quests remain reviewable and future quests are locked. Success unlocks the next quest, while the frontend waits for an explicit “下一关” action to advance the displayed task. A chapter reflects the supplied source context, not guaranteed coverage of an entire large project.

Generation now makes separate bounded model requests for source analysis, one chapter outline, each quest and final consistency review. Validated partial results are private persistent drafts. The same explicit campaign POST resumes compatible work after failure or interruption; GET only reads progress and never starts generation. All quests remain unavailable until the complete chapter passes validation and publishes atomically. The chapter response timeout applies separately to each model call.

## HTTP (frontend target)

- DELETE `/projects/{project_id}` -> `{deleted:true,project_id:string,message:string,cleanup_pending?:boolean}`. Requires the same per-project operation lock as generation/run/repair; concurrent mutation returns 409. Removes canonical app-owned practice/baseline directories and exact project records, including evaluation/quest-archive id-colon prefixes. Imported sources, other archives, shared settings and credentials remain. Unknown valid id returns 404; invalid id or unsafe path returns 400. A durable deletion journal restores pre-commit interruptions before archives are served and retries post-commit cleanup. `cleanup_pending:true` means logical deletion committed but residual owned files need later cleanup; display its message. The frontend confirms the named archive, removes only that id's draft/backups after success, and clears current selection if deleting the active archive.

- GET `/health` -> `{status:'ok', version:string}`.
- GET `/settings` -> `{base_url:string, model:string, key_set:boolean, storage:string,generation_timeout:number,storage_error?:string}`. Production storage is `system-keyring` or `windows-dpapi`; unreadable credentials return `unavailable` with a safe `storage_error`. `session` is only an explicitly disabled-keyring test mode. Windows prefers the native vault and falls back to current-user DPAPI encryption; macOS uses Keychain. No plaintext or silent session fallback. Public responses never contain credential values.
- PUT `/settings` body `{base_url,model,api_key?:string,generation_timeout?:number}` -> same public settings. Omitted api_key preserves key; empty string removes it. Never return key. Chapter response read timeout defaults to 300 seconds, accepts integer 30–900; omission preserves the saved value. Connectivity reads use 30 seconds, other model requests 90; connect/write/pool limits are 15/30/15 seconds. These are HTTPX phase/inactivity limits, not a total request deadline.
- POST `/settings/test` -> `{ok:boolean,message:string}`; uses saved settings and real configured service.
- GET `/runtime` -> managed instance identity (`schema`, `project_root`, `data_root`, `pid`, `port`, `instance_id`, `started_at`), never the control token. POST `/runtime/shutdown` -> 202 `{stopping:true,instance_id}` followed by graceful shutdown. Both require a loopback peer and `X-Codewarden-Runtime-Token` matching local runtime metadata. Unmanaged instances return 404, failed authentication 403, busy/already closing 409. Shutdown and operation admission share a lock; new guarded operations receive 503 once shutdown starts. These endpoints serve the local restart script, not browser UI.
- GET `/projects` -> `Project[]`.
- POST `/projects/import` body `{path?:string,git_url?:string}` -> Project.
- POST `/projects/demo` body `{language:string}` -> Project imported from authored example source. Import itself needs no model; the current frontend then requires role selection and real AI chapter generation, just as for user projects. Legacy authored fixture quests are not a substitute for generated campaigns.
- GET `/projects/{id}` -> Project.
- POST `/projects/{id}/refresh` -> Project (detect original changes, report stale; preserve edits).
- GET `/projects/{id}/files` -> `FileInfo[]`.
- GET `/projects/{id}/file?path=...` -> `{path,content,original,hash,language}`; original is imported baseline.
- PUT `/projects/{id}/file` body `{path,content,expected_hash?:string}` -> `{path,content,hash}`; optimistic conflicts return 409.
- GET `/projects/{id}/quests` -> `Quest[]`.
- POST `/projects/{id}/generate` -> `Quest[]` (real AI; may take time).
- GET `/projects/{id}/campaign` -> Campaign; POST the same path with `{role:'mage'|'paladin'|'druid'}` to generate or retrieve the selected chapter. See the campaign contract for states, retries and role-change restrictions. The current frontend uses this flow rather than legacy generation.
- POST `/projects/{id}/quests/{qid}/answer` body `{answer:string}` -> `{correct:boolean,feedback:string,quest:Quest}`.
- POST `/projects/{id}/quests/{qid}/hint` -> `{hint:string,used:number}`.
- POST `/projects/{id}/quests/{qid}/review` body `{answer?:string}` -> AI evaluation of an explanation or code diff, as specified in the campaign contract. Explanation review needs a model; code review additionally requires actual source changes and a successful run of the current version. Failed review does not unlock the next quest.
- POST `/projects/{id}/run` body `{quest_id?:string,entry?:string}` -> RunResult; actual run may take up to 30s, UI waits. `run` quests complete only against their declared entry and verified expected result; `review` quests record the run and still require a separate AI review. AI feedback is never reported as subprocess output or proof of execution.
- POST `/projects/{id}/stop` -> `{stopped:boolean}`.
- GET `/projects/{id}/runs` -> RunResult[] most recent first.
- POST `/projects/{id}/repair` -> `{status:string,message:string,attempts:object[],changes:{path,before,after}[]}`. Environment/build only, max3, no global changes. UI can show JSON details.
- POST `/projects/{id}/undo-repair` -> `{message:string}`; refuse if learner edited after repair.
- GET `/knowledge?q=&language=` -> Card[].
- PUT `/knowledge/{id}/favorite` body `{favorite:boolean}` -> `{ok:true}`.

Project: `{id,name,source_path,workspace,languages:string[],file_count:number,created_at:string,stale:boolean,demo:boolean,completed:number,total:number}`.
FileInfo: `{path,language,size:number,symbols:Symbol[]}`.
Symbol: `{name,kind,line:number,end_line:number}`.
Quest: `{id,type:'explore'|'predict'|'repair'|'feature',title,brief,objective,source_file,line:number,expected_output?:string,status:'locked'|'available'|'completed',hints_used:number,origin:'demo'|'ai',verification:string}` plus campaign fields `order`, `verification_mode:'answer'|'explain'|'run'|'review'`, `steps`, `concepts`, `success_message` and optional `run_entry`, as defined in the campaign contract. Private answer/hints/rubric and grading baselines are NOT sent in Quest. UI should source jump on source_file. Source reference required.
RunResult: `{id?:string,status:'passed'|'failed'|'missing_tool'|'blocked'|'timeout'|'cancelled',command:string[],exit_code:number|null,stdout:string,stderr:string,duration_ms:number,quest_completed?:boolean}`.
Card: `{id,language,title,summary,body,code,output,pitfalls:string[],tags:string[],version,source_url,reviewed_at,favorite?:boolean}`. Plain text, no unsafe HTML.

Saved chapter reading, packaged knowledge, answer checks and local run verification can work offline when the required tools and dependencies are available. Chapter generation, explanation review, code-diff review and model-assisted build diagnosis require the configured model. Local execution is not an OS sandbox; workspace copies, path checks and bounded subprocesses do not guarantee containment of arbitrary program behavior. Runtime and build support remains limited to supported entrypoints and available Windows/macOS tools, with no automatic system installs or global environment changes.

## Python modules (dicts; no coupling to API/store)

`backend.languages`:
- `detect_language(path: str) -> str | None`
- `analyze_file(path: str, content: str) -> dict` returns FileInfo (size from utf8 content).
- `suggest_entry(files: list[dict], language: str | None = None) -> str | None` select appropriate source entry, not config file.
- `demo_quests(language: str) -> list[dict]`: legacy authored fixtures with extra private `answer`, `hints:list[str]`, and `expected_output` for tests/compatibility. These do not replace AI chapters in the current frontend. No counterfeit runtime.
- `demo_path(language: str) -> pathlib.Path` path under examples/. Example source starts with an authored practice bug and emits a stable output; importing it opens the same role/chapter flow as user source. Expected_output in run-graded quests is matched exactly after strip; successful execution must have exit0.

`backend.knowledge`: `list_cards(query: str='', language: str='') -> list[dict]` loads packaged data/knowledge.json, no model/network. Root adds persisted favorite fields.

`backend.runner`:
- `plan_run(workspace: pathlib.Path, entry: str) -> list[list[str]]` commands to compile/run/test supported entry. MUST use real discovered executables; shell=False. Missing executables raise `RuntimeError` with useful Chinese message. Invalid paths/unsupported/unsafe plans raise ValueError. Entry may be a known source or project test configuration if supported.
- `run_project(workspace: pathlib.Path, entry: str, *, run_key: str, timeout: float=30) -> dict`: returns RunResult. Must return actual stdout and stderr, cap output; no model dependency. Store process handles globally only for stop; clean after completion. Detect risky local side effects and refuse automatic known-dangerous commands; never represent this as full sandbox.
- `stop_run(run_key: str) -> bool` stop process tree.
- `validate_patch_path(workspace: pathlib.Path, relative: str) -> pathlib.Path` rejects escape/absolute/symlink/junction and originals. Root repairs use it.

Root provides backend package and dependencies. Workers can use Python standard library plus installed dependencies; no global/system installs. Worker tests should use unittest or pytest. Do not edit another worker's files or root dependency configs.
