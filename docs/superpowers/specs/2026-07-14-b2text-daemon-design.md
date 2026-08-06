# B2text 服务化（HTTP daemon + CLI 客户端）设计文档

**日期**: 2026-07-14
**状态**: 设计中
**作者**: Claude（与用户协作）
**前置依赖**: 已完成 2026-07-10-b2text-design（CLI 一次性转写）

## 背景

当前 `bilibili_to_text.py` 是单次执行的 CLI：每次跑都要传参、下载、转写、写文件，且 FunASR 模型每次都重新加载（约 30–60 秒）。当用户需要批量处理多个 BV、或者批量抓取某个 UP 主的所有视频时，这种交互方式效率很低，且无法同时追踪多个任务的状态。

## 目标

- 起一个本地常驻 daemon，FunASR 模型**只加载一次**，常驻内存
- 通过 CLI 命令向 daemon 提交任务（单个 BV 或整个 UP 主）
- 任务异步执行：提交后立即返回 task_id，可单独查状态、列任务
- 复用 2026-07-10 设计中的 `transcriber`、`normalizer`、`formatter`、`bili_api`、`audio`，**不重写核心转写逻辑**
- 单机单用户使用，macOS 本地优先

## 非目标（YAGNI）

- ❌ Web UI（HTTP 端点仅供命令行客户端和 curl 调试）
- ❌ 多用户/认证（单机单用户）
- ❌ 分布式部署（仅本地监听 `127.0.0.1`）
- ❌ 实时流式转写（音频必须先下完）
- ❌ 任务优先级/抢占
- ❌ Cookie 自动刷新（cookie 过期用户手动重启 daemon）
- ❌ 多 worker 并行（MPS 模型只能串行）
- ❌ 重构现有 `bilibili_to_text.py` 的核心逻辑

## 架构

```
                                  ┌────────────────────────────────┐
                                  │  Daemon (`b2text serve start`) │
                                  │  ┌──────────────────────────┐  │
   CLI (httpx)                    │  │ FastAPI on 127.0.0.1:8765 │  │
   b2text transcribe/...  ─HTTP─► │  │   POST   /transcribe     │  │
   b2text status / list          │  │   GET    /tasks/{id}     │  │
                                  │  │   DELETE /tasks/{id}     │  │
                                  │  │   GET    /health         │  │
                                  │  └──────────┬───────────────┘  │
                                  │             ▼                  │
                                  │  SQLite (jobs 持久化表)         │
                                  │             ▼                  │
                                  │  ┌──────────────────────────┐  │
                                  │  │ Worker（单 asyncio 协程）  │  │
                                  │  │   FunASR 模型常驻内存     │  │
                                  │  │   顺序消费 asyncio.Queue │  │
                                  │  └──────────────────────────┘  │
                                  └────────────────────────────────┘
```

### 模块拆分（新增）

| 文件 | 职责 |
| --- | --- |
| `b2text/server.py` | FastAPI app + 路由；启动时初始化 worker 协程 |
| `b2text/worker.py` | 后台 worker 协程；从队列取任务，调用 transcriber，写结果文件 |
| `b2text/queue.py` | SQLite-backed 任务队列；提供 enqueue/claim/finish/fail/recover API |
| `b2text/cookie_store.py` | 读 cookie：文件优先，`B2TEXT_COOKIE` env 兑底 |
| `b2text/upmaster.py` | UP 主视频列表抓取 + 批量展开成多个 bv 子任务；函数签名 `fetch_up_videos(uid: int, limit: int) -> list[str]` 返回 bvid 列表 |
| `b2text/client.py` | CLI 公用：本地直跑 or HTTP 提交到 daemon |

### 改动

| 文件 | 改动 |
| --- | --- |
| `bilibili_to_text.py` | 改成薄壳：根据 argv 调度——直接调用本地逻辑（`b2text run`）、或走 daemon（`b2text transcribe` / `b2text status` / `b2text list`） |
| `requirements.txt` | 加 `fastapi`、`uvicorn`、`httpx` |
| `README.md` | 文档化新命令、`serve` 生命周期、Cookie 放置 |

## 数据模型

### Job 表（SQLite）

```sql
CREATE TABLE jobs (
    id           TEXT PRIMARY KEY,        -- UUID4
    type         TEXT NOT NULL,           -- 'bv' | 'up'
    target_id    TEXT NOT NULL,           -- bvid 或 uid
    output_dir   TEXT NOT NULL,
    limit_n      INTEGER,                 -- up 时有效，bv 时为 NULL
    status       TEXT NOT NULL,           -- 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
    parent_id    TEXT,                    -- up 任务的子任务指向父 id
    result_path  TEXT,                    -- done 时填写
    error        TEXT,                    -- failed 时填写
    created_at   REAL NOT NULL,           -- epoch seconds
    started_at   REAL,
    finished_at  REAL,
    retry_count  INTEGER DEFAULT 0
);
CREATE INDEX idx_status  ON jobs(status);
CREATE INDEX idx_created ON jobs(created_at);
```

### 端点

```
POST   /transcribe
  body: { "type": "bv"|"up", "id": "BV1xxx"|"12345678", "output_dir": "...", "limit"?: 50 }
  resp: { "task_id": "uuid" }
  错误: 503 (模型未就绪)、400 (id 格式)、429 (队列过长，可选)

GET    /tasks
  query: ?status=running&limit=50&offset=0
  resp:  { "tasks": [...], "total": 123 }

GET    /tasks/{id}
  resp:  { id, type, target_id, status, result_path?, error?, created_at, started_at?, finished_at?, parent_id? }

GET    /tasks/{id}/log
  resp:  { "logs": [...每一行 JSON Lines 解析后的对象...] }
  返回该 job 的全部结构化日志；用于查完整 stacktrace（`GET /tasks/{id}` 的 error 字段不带栈）

DELETE /tasks/{id}
  仅对 status=queued 有效；running 任务会标记 cancelled 但等当前 step 跑完
  resp: 204 或 409

GET    /health
  resp:  { "ok": true, "model_loaded": true, "queue_len": 3, "running": 1 }
  模型未就绪时 ok=false、http 503
```

### CLI

```bash
# daemon 生命周期
b2text serve start          # 后台启动，写 pidfile ~/.config/b2text/daemon.pid
b2text serve stop           # SIGTERM，等待 worker 优雅退出
b2text serve status         # 状态/模型/队列长度
b2text serve logs           # tail ~/.local/share/b2text/daemon.log（默认 tail -n 50）

# 提交任务（走 daemon）
b2text transcribe BV1xxx -o /Users/.../sourceRead/
b2text transcribe --type up <uid> -o /Users/.../sourceRead/ --limit 50
b2text status <task_id>
b2text list                 # 列出所有 active 任务
b2text cancel <task_id>

# 逃生口（不走 daemon，本地同步跑）
b2text run BV1xxx -o ...
```

## 关键流程

### 1. Daemon 启动

1. `b2text serve start`
2. 子进程 `python -m b2text.server --port 8765`
3. 加载 cookie（file → env，缺则 stderr + 退出码 4）
4. 打开 SQLite（`~/.local/share/b2text/jobs.db`）
5. 从 SQLite 恢复 queued 任务到内存 Queue（`recover_pending`）
6. 启动 worker 协程（先开始加载 FunASR 模型，约 30–60s）
7. `GET /health` 在模型未就绪时返回 `ok=false, http=503`，模型就绪后转为 `ok=true, http=200`
8. pid 写入 `~/.config/b2text/daemon.pid`

### 2. 提交单 BV（pipeline 七步）

1. CLI HTTP `POST /transcribe` `{type:"bv", id, output_dir}`
2. Server 校验 id、output_dir 存在、模型 ready → 入 SQLite + Queue → 返回 task_id
3. Worker 协程从 Queue 取任务
4. `get_video_info` — `bili_api.get_video_info`
5. `get_audio_url` — `bili_api.get_audio_url`
6. `download_audio` — `curl` 下载 m4s
7. `convert_wav` — `ffmpeg` 转 16k mono wav
8. `chunk_audio` — 视时长决定（>10 min 时切 5 min 片段）
9. `transcribe` — `transcriber.transcribe`（每个 chunk 一条日志）
10. `normalize_write` — `normalize_funasr_output` + `format_segments` + 写 txt
11. SQLite 更新 `status=done, result_path=..., finished_at=...`

### 3. 提交 UP 主

1. CLI HTTP `POST /transcribe` `{type:"up", id:<uid>, output_dir, limit:50}`
2. Server 创建父任务 `parent_id`=NULL → 入队
3. Worker 取到父任务：
   - 调用 `upmaster.fetch_up_videos(uid, limit)` → 拿到 BV 列表
   - 每个 BV 创建一个子任务 `parent_id`=父id → 入队
   - 父任务状态 `done`（本身没有 result_path）
4. 子任务按正常单 BV 流程处理
5. CLI 可查父任务 `/tasks/<parent_id>` 知道子任务列表

### 4. Cookie 读取

读取优先级（在 daemon 启动时一次性确定）：
1. `~/.config/b2text/cookie` 文件（明文，权限 600）
2. `B2TEXT_COOKIE` 环境变量
3. 都没有 → 退出码 4，stderr 提示：`请创建 ~/.config/b2text/cookie 并填入 SESSDATA=...; bili_jct=...`

`b2text serve start` 检测不到文件时**额外提示**：
```
💡 提示：把 cookie 写入 ~/.config/b2text/cookie（明文，文件建议 chmod 600）
   内容：SESSDATA=xxx; bili_jct=xxx
```

### 5. 错误处理

### 重试策略

| 触发 | 重试策略 | `retry_count` 是否 +1 |
| --- | --- | --- |
| B 站 API 调用（`get_video_info`、`get_audio_url`）返回 `code != 0` 或网络超时 | 单任务 retry 3 次（指数退避 1s / 4s / 16s），3 次全失败 → `status=failed` | ✅ 是 |
| `download_audio`（curl）失败 | 不重试，直接 `status=failed`，记 curl exit code + stderr 末尾 | ❌ 否 |
| `convert_wav` / `chunk_audio`（ffmpeg）失败 | 不重试，直接 `status=failed`，记 ffmpeg exit code + stderr 末尾 | ❌ 否 |
| `transcribe`（FunASR）异常 | 不重试，直接 `status=failed`，记异常 message + stacktrace | ❌ 否 |
| 孤儿任务（daemon kill 后 status=running） | 重启时重置为 `status=queued`，**retry_count 不变**（孤儿恢复不算新一次重试） | ❌ 否 |

### 其他错误与状态

| 场景 | 行为 |
| --- | --- |
| 模型未就绪时 `POST /transcribe` | 503 + body `{"error":"model_loading"}` |
| User 取消（DELETE /tasks/{id}，仅 queued） | 立刻 status=cancelled |
| Daemon 重启时还有 queued 任务 | worker 启动后自动 recover，重新入队 |

## 并发与持久化

- 单 worker（asyncio 协程），串行处理任务
- 任务在 SQLite 持久化：状态变更、创建/开始/结束时间、错误信息
- daemon 重启后从 SQLite 恢复 queued 任务到内存 asyncio.Queue
- 内存中还有一份 asyncio.Queue（不持久化 in-flight 状态，避免双源）

## 测试

| 测试文件 | 覆盖 |
| --- | --- |
| `tests/test_queue.py` | enqueue / claim / finish / fail / recover / cancel |
| `tests/test_cookie_store.py` | 文件优先 / env 兑底 / 缺时抛错 |
| `tests/test_upmaster.py` | mock bili_api，验证 fan-out 行为 |
| `tests/test_server.py` | FastAPI TestClient；POST /transcribe / GET /tasks/{id} / DELETE；503 on 未就绪 |
| `tests/test_worker.py` | mock transcriber；跑通单任务和 up fan-out |

## 日志

> **为什么这一节这么细**：daemon 模式下没有 stdout 给用户看，也没有交互式确认。日志是判断 daemon 健康、定位卡住、复盘失败的**唯一可观测性**。HTTP 状态码只能告诉成功/失败，不能告诉在哪一步卡住、ASR 实际跑成什么样。

Worker 必须有**清晰、可追责**的日志——每条任务的关键步骤、当前进度、错误细节都要能查。

### 日志输出位置

| 模式 | 输出位置 |
| --- | --- |
| daemon 后台运行 | `~/.local/share/b2text/jobs.log`（JSON Lines 格式） + stderr |
| `b2text run <bv>` 本地同步运行 | stdout（人友好格式 + emoji）+ stderr |
| `b2text serve logs` | `tail -n 50 -F` 该 log 文件 |

### 日志格式

```json
{"ts": "2026-07-14T12:34:56.789", "level": "INFO", "job_id": "uuid", "step": "fetch_audio_url", "msg": "fetching", "extra": {"bvid": "BV1xxx", "aid": 12345, "cid": 67890}}
{"ts": "2026-07-14T12:35:01.234", "level": "INFO", "job_id": "uuid", "step": "fetch_audio_url", "msg": "ok", "extra": {"url_host": "..."}}
{"ts": "2026-07-14T12:35:10.456", "level": "INFO", "job_id": "uuid", "step": "download_audio", "msg": "ok", "extra": {"size_bytes": 1234567, "duration_sec": 5.4}}
```

每条日志必含：`ts`、`level`、`job_id`、`step`、`msg`。`step` 是下文七步之一。`extra` 按 step 携带结构化数据。

**失败时的 `extra` 必填字段**（即便 step 不在下方七步之内，只要 `level=ERROR` 就必须带）：
- `exc_type`：异常类名（如 `funasr.AutoModel.AutoModelError`、`requests.Timeout`）
- `exc_message`：异常 message 原文
- `stacktrace`：完整 Python stacktrace（`traceback.format_exc()`）

### 必须记录的步骤（pipeline 七步）

worker 每跑一个任务，**按顺序**记录每一步的 `start` + `end/ok/fail`：

| step 名 | 含义 | extra 关键字段 | 失败时记录 |
| --- | --- | --- | --- |
| `get_video_info` | 拉取 BV 元数据 | `bvid`、`aid`、`title`、`pages_count` | `api_code`、`api_message`、HTTP 状态、完整 stderr |
| `get_audio_url` | 解析音频直链 | `aid`、`cid`、`url_host` | `api_code`、`api_message` |
| `download_audio` | 下载 m4s 文件 | `size_bytes`、`duration_sec`、`http_status`（如可获取） | curl exit code、stderr 末尾 500 字符、`output.exists()`/`stat.st_size==0` 检查结果 |
| `convert_wav` | ffmpeg 转 16k mono wav | `duration_sec`、`size_bytes` | ffmpeg exit code、stderr 末尾 500 字符 |
| `chunk_audio` | 切分为 ≤300s 片段 | `chunk_count`、`chunk_threshold_sec`、`chunk_size_sec` | ffmpeg exit code |
| `transcribe` | FunASR 调用（每个 chunk 一条） | `chunk_index`、`chunk_count`、`duration_sec`、`time_speech`、`time_escape`、`rtf`、`segment_count`、`spk_count` | FunASR 异常 message + stacktrace、行号；MPS 警告（`NotImplementedError`）；`Missing punc_model` 警告 |
| `normalize_write` | 规范化 + 写 txt | `segment_count`、`speaker_count`、`output_path`、`file_size_bytes` | 写文件 IOError、规范化抛出的异常 |

### ASR 步骤的细节

`transcribe` 这步是整个 pipeline 最慢、最容易出错、最容易让用户以为"卡住了"的步骤，单独要求：

1. **每个 chunk 单独一条日志**：包括 `chunk_index/total`、`duration_sec`、FunASR 返回的 `time_speech`、`time_escape`、`rtf`
2. **最终汇总日志**：合并后 `aggregate_segments_count`、`aggregate_duration`
3. **异常保留**：punc_model 缺失、MPS 未实现、segfault（如果在 chunk 路径下还遇到）都保留 warning 级别
4. **首次加载日志**：worker 启动时模型加载过程独立记录——`funasr_load`、`funasr_loaded`、`funasr_failed` 三条，整体耗时

### 失败任务的现场保留

`status=failed` 时 log 必须留下：
- 失败所在 step
- 完整异常（`exc_type`、`exc_message`、`stacktrace`）
- 失败前的最后一步成功状态（让用户能定位"上一步还在哪里、这一步为何挂"）
- B 站 API 失败的 `code` 和 `message` 原文

这些以结构化字段写到 log，worker 退出前再写一条 `job_done status=failed` 总收尾。

### 用户查询接口

- 实时看日志：`b2text serve logs`（`tail -F`，可 `-n` 指定初始行数，默认 50；多 job 并发时日志会交错，v1 不做切分）
- 单 job 查询：`GET /tasks/{id}` 返回的 `error` 字段给出**失败 step + 异常 message**（不带 stacktrace，避免暴露内部）
- 详细 stacktrace：`GET /tasks/{id}/log` 返回该 job 的全部结构化日志行（含 `extra.stacktrace`）

### 不打算在第一版做的

- ❌ 结构化日志的多 sink（不发 Kafka、不上 sentry）
- ❌ 日志轮转/压缩（单文件追加足够，磁盘涨到 GB 再考虑）
- ❌ 按 level 过滤（默认 INFO 全量）

## 风险与权衡

- **端口冲突** 选 8765（罕见冲突）；如冲突允许 `--port` 覆盖
- **MPS 内存峰值**：模型常驻约 2–3 GB，10 分钟音频峰值再 +1 GB，可接受
- **Cookie 在文件里**：明文；只放在 `~/.config/b2text/cookie`（macOS 下此目录不进 iCloud 备份，否则需用户提示）
- **PID 文件**：仅用于 CLI 启停；用户 `kill -9` 后 CLI `start` 会先检查 pid 文件并 warn
- **崩溃后任务状态恢复**：用 SQLite 事务保证一致性，worker 重启会清理 status=running 的孤儿
