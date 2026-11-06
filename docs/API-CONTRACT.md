# 接口约定

当前流程见[学习流程](LEARNING.md)。所有业务接口以 `/api` 为前缀，使用 JSON；本机交互文档位于 `/api/docs`。错误通常为 `{detail:string}`；参数无效为 422，记录不存在为 404，冲突或正在处理的操作为 409，关闭中的服务拒绝新写入并返回 503。部分业务状态错误返回 400。

## 设置与本机服务

| 方法与路径 | 请求 / 结果 |
| --- | --- |
| GET `/health` | `{status:'ok',version:'0.3.0'}` |
| GET `/settings` | 公开模型设置，不含密钥 |
| PUT `/settings` | `{base_url,model,api_key?,generation_timeout?}`；返回公开设置 |
| POST `/settings/test` | 使用已保存接口进行真实短请求，成功返回 `{ok:true,message}` |
| GET `/runtime` | 受管理实例身份 |
| POST `/runtime/shutdown` | 空闲时返回 202 `{stopping:true,instance_id}` 并正常关闭 |

公开设置包含 `base_url`、`model`、`key_set`、`storage`、`generation_timeout`，必要时包含安全的 `storage_error`。更新省略 `api_key` 保留密钥，空字符串删除密钥。生成读取超时默认 300 秒，接受 30–900 的整数；省略则保留设置。凭据使用系统凭据管理器或 Windows DPAPI，不返回密钥，不静默明文回退。

`/runtime` 两个接口仅供启动管理脚本：要求回环来源及 `X-Codewarden-Runtime-Token`，返回值不含控制令牌。未登记实例返回 404，认证失败返回 403，忙碌或已关闭中返回 409。关闭与任务准入共享锁，不按端口强杀服务。

## 材料导入与分析

| 方法与路径 | 请求 / 结果 |
| --- | --- |
| POST `/imports` | `{path?,files?,git_url?,encoding?}` → 材料 |
| GET `/imports` | 尚未分配给冒险的材料列表 |
| GET `/imports/{iid}` | 材料及真实处理状态 |
| PUT `/imports/{iid}/scope` | `{paths:string[],page_start?:number,page_end?:number}` → 材料 |
| POST `/imports/{iid}/analyze` | `{focus?:string}` → 202，显式后台分析 |
| GET `/imports/{iid}/file?path=...` | `{path,content,language,hash}`，只读导入快照 |
| DELETE `/imports/{iid}` | `{deleted:true}`，使用中或分析中拒绝删除 |

导入只接受 `path`、`files`、`git_url` 中一种。上传文件为 `{path:string,data_base64:string}` 数组；不采用 multipart。`encoding` 默认 `utf-8-sig`，另支持 `utf-8`、`utf-16`、`gb18030`。Git 地址接受无凭据 HTTPS 或 `git@host:owner/repo`，由本机 Git 进行有界浅克隆，不运行仓库代码。

材料包含 `id,title,files,diagnostics,snapshot_hash,created_at,status,error,scope,scope_revision,coverage`，分析成功后包含 `analysis`。文件条目含 `path,language,chars,hash`，PDF 另含 `pages`。状态为 `imported/analyzing/analyzed/failed`；分析失败原因持久化，GET 不触发重试。

`analysis` 含 `summary,content_type,recommendations,outline`；推荐为 1–3 项 `{role,reason,focus}`，知识概览为 `{title,summary,source_ids}[]`，引用可定位的原文。职业 ID：`mage`、`paladin`、`druid`、`shaman`、`priest`。

`coverage` 含 `total_chars,selected_chars,batch_count,max_batch_chars`；`batch_count` 是预计批次数。旧字段 `context_chars,truncated` 仅描述兼容预览，不代表完整分析。所选可读原文按文件顺序无损分批，默认每批最多 24,000 字符及 16,000 字节 UTF-8 JSON；系统还会按实际模型请求大小自动减小批次。所有原文批次成功后，分组、分层汇总为整体概览。

`analysis_progress` 为 `{phase,total_batches,completed_batches,failed_batches,processed_chars,total_chars,current_batch?}`；阶段为 `reading/summarizing/complete/failed`，批次数以此处实际值为准。`analysis_complete` 仅在原文读取与汇总全部成功后为真；进度不等于模型理解程度或玩家学习进度。内部批次检查点不公开。失败后显式分析只重试未完成部分；刷新和重启不触发模型调用。

`analysis_focus` 保存本次关注重点，最多 2,000 字符；分析请求省略 `focus` 则继续使用已保存值，空字符串明确清除。材料范围、关注重点、模型身份或分析提示词变化时重新分析，不混用旧检查点。范围更改会清除分析并增加修订；已分配或分析中的材料不能修改范围。

容量：512 文件、总计 20 MB，文字单文件 2 MB，PDF 单文件 8 MB / 500 页，提取总文字 300 万字符。隐藏、凭据、链接及常见二进制/构建目录过滤。`pypdf` 仅提取 PDF 文字，不执行 OCR；无法提取页通过 `diagnostics` 说明。

## 独立角色与冒险

| 方法与路径 | 请求 / 结果 |
| --- | --- |
| GET `/roles` | 五职业 `{id,name,focus,description}[]` |
| GET `/characters` | 角色列表 |
| POST `/characters` | `{role,name?}` → 角色；该职业已有角色时返回已有角色 |
| DELETE `/characters/{cid}` | 删除角色及全部所属冒险 |
| GET `/characters/{cid}/adventures` | 该角色的冒险列表 |
| POST `/characters/{cid}/adventures` | `{import_id}` → 独立冒险 |
| GET `/adventures/{aid}` | 冒险公开状态 |
| DELETE `/adventures/{aid}` | 删除冒险及练习副本，材料快照重新成为未分配材料 |
| POST `/adventures/{aid}/prepare` | 202，规划主题目标并逐个准备最初最多两个开放任务 |
| GET `/adventures/{aid}/journal` | `{entries:[{task_id,title,answer,feedback,status,hints_used,updated_at,reflection?}]}` |

只有已分析且推荐该职业的材料可创建冒险；材料只能分配一次。角色含 `id,role,name,adventure_count,completed_objectives,completed_adventures,stage_title,created_at`。冒险含 `id,character_id,import_id,role,title,summary,status,topics,objectives,tasks,recommended_task_id,completed_objectives,total_objectives,created_at`，必要时含 `error/ending`，并含 `story_events`：`{id,topic_id?,title,body,created_at}[]`。主题和 ending 事件按真实完成目标幂等生成。

冒险状态为 `unprepared/planning/ready/failed/completed`。主题为 `{id,title,description}`；目标含 `id,topic_id,title,description,source_ids,prerequisite_ids,required,status`，状态为 `locked/available/completed`。数量由材料决定，上限为 30 主题、60 目标；校验来源、标识、前置无环及至少一个必要目标。必要目标全部完成才形成结语，不按任务数组序号结算。

只有完整分析后才允许创建新冒险。旧的大材料前缀分析须重新分析；已有但尚未规划的冒险会在显式准备时补齐材料分析，公开 `source_analysis_progress` 与 `source_analysis_complete`。规划使用整体概览，任务和评审按来源 ID 读取对应原文，包含材料后部。

任务独立发布。某任务失败不撤回其他已发布任务；规划已存在时不重复规划。服务启动将中断中的生成标为失败，等待显式重试，不隐式恢复模型调用。

## 任务、评审与练习副本

以下任务路径均以 `/adventures/{aid}/tasks/{tid}` 为前缀。

| 方法与后缀 | 请求 / 结果 |
| --- | --- |
| GET 空后缀 | 公开任务 |
| POST `/prepare` | `{replace?:boolean}` → 202，准备、重试或显式替代 |
| POST `/hint` | `{hint,hints_used}`，消耗下一条已保存提示 |
| POST `/answer` | `{answer:string}` → AI 评审及更新后的任务 |
| PUT `/reflection` | `{confidence,note}` → `{confidence,note,updated_at}` |
| GET `/file?path=...` | 读取该任务练习文件 |
| PUT `/file` | `{path,content}`，保存该任务练习文件 |
| POST `/run` | `{entry?:string}` → 真实执行结果 |
| POST `/stop` | `{stopped:boolean}` |

任务准备状态 `status` 为 `unprepared/generating/ready/failed`，与学习状态 `learning_status` 的 `locked/available/practicing/completed` 分离。已准备任务含 `title,brief,objective,teaching,question,interaction,options?,source_refs,criteria,hints_used,used_hints,run_entry?`，以及已保存的 `last_answer/last_evaluation/last_run` 等实际记录。互动为 `explain/choice/order/code`，教学项为 `{title,body}`，选项为 `{id,text}`，引用含 `id,path,line,page?`。答案最多 12,000 字符，保存文件最多 1,048,576 字符。

公开响应不包含答案基准、未使用提示、生成令牌/身份、提示词快照或完整尝试内部字段。替代任务保留旧版本与副本并产生新任务 ID，旧版本不出现在公开当前任务列表中；同目标公开标准与执行要求保持稳定。

评审返回 `{status,feedback,checks,evaluation:'ai_review',task}`，`status` 为 `passed/needs_work/pending`，检查项 `{criterion,passed,evidence}` 必须逐项覆盖公开 `criteria`。模型失败或无效结果转为 `pending`，回答及尝试已保存。通过后完成对应目标；重复提交不重复增加目标数。自评通过 PUT `/reflection` 保存：`confidence` 为 `needs_review/understood/can_apply`，`note` 最多 2,000 字符。公开任务含 `reflection`，日志条目可含 `reflection`；没有作答也能保存自评并产生可查看记录。自评不调用模型、不完成目标，`needs_review` 可调整未完成任务推荐优先级。

代码任务提交前要求副本已有实际改动，当前版本的真实运行 `status='passed'` 且退出码为 0。评审发送实际改动与运行证据；版本变化会拒绝使用旧执行证据。运行结果含 `status,command,exit_code,stdout,stderr,duration_ms,id,workspace_version,evidence_kind:'local_execution'`，不把 AI 文本冒充子进程输出。单次运行超时为 30 秒。

每任务独立练习目录，按需从材料快照复制可用文字文件，PDF 不复制为代码练习文件。路径必须留在受管理副本内，拒绝链接与越界。运行仍拥有当前用户权限，不是操作系统沙箱。执行适配与工具发现见 `backend/runner.py`；不提供旧知识卡查询、旧战役、旧项目档案或自动修复 API。

## 模型请求与持久化

分析、规划、生成、评审都是显式操作。材料分析可包含多个原文与汇总请求；每个模型请求的可重试传输或格式兼容错误共享最多两次尝试，不进行无限重试。完整模型请求限 32,000 字节；原文自动减小批次，摘要分组归并。单个知识目标引用原文过大时，规划自动纠正一次，将目标拆小；仍不满足限制则保留材料分析并报告失败。答案或评审证据超限明确报错，不发送残缺证据；运行证据单独限 4,096 字节。结构校验不能保证 AI 事实正确。

新状态使用 `imports_v2`、`characters_v2`、`adventures_v2` 与受管理 `sources_v2/practice_v2`。启动清理已废弃的旧项目、知识库、战役、草稿、评审与运行历史及已知旧副本目录，不扫描原始来源路径，不删除模型设置与凭据，不创建旧版兼容视图。此变更不向旧 API 提供兼容层。
