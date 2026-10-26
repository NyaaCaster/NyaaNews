# 猫猫报纸（NyaaNews）开发计划 SSOT

版本：V1
状态：开发中，P1 完成；P2 进行中。
更新日期：2026-10-26。

本文是实现阶段唯一事实来源。与早期设计审核草案冲突时，以本文为准；历史验证和私有部署细节见 `.docs/RSS验证-20261026.md`、`.docs/技术验证-001.md`、`.env/部署设计.md`。

## 1. 项目目标

每天北京时间 08:00，采集前一自然日的新闻。系统通过 RSS/Atom 订阅发现候选，直接抓取公开原文，提取原语言正文，按主题和去重规则生成 Markdown，导入 配置指定的 AstrBot 知识库。

V1 每个启用主题最多 3 篇，当前 11 个主题理论上最多 33 篇；当日没有合格文章时不补历史内容。

## 2. 已确认边界

- 生产新闻来源：RSS/Atom。dsh、新闻 MCP、新闻 API 和 GDELT 不属于默认生产链路。
- 每篇文档保存原始标题、公开原文、来源 URL、canonical URL、原文发布时间、订阅时间和抓取时间；不翻译、不摘要、不改写。
- 主题、订阅 URL、来源权重、语言和实际配额放在 `.env/`，不写入公开代码。
- 正式文件、状态、日志和待重试记录位于 `E:/DockerRes/NyaaNews/`，通过 Docker bind mount 映射到 `/data/`。
- 容器临时目录为 `/tmp/nyaanews/<run-id>/`；任务结束和下次启动清理遗留中间文件。
- 文章正文不得写入 Git 或镜像；`.env/`、`.ref/`、`data/` 和外部盘内容不进入公开仓库。

## 3. 已验证基线

| 能力 | 结果 |
| --- | --- |
| RSS/Atom 11 源轮询 | 通过 |
| 11 主题各 3 篇候选 | 通过 |
| 33 篇公开原文抓取 | 通过 |
| 33 篇 Markdown 落盘到 E: 外部盘 | 通过 |
| dsh/MCP | 已验证可用，但不纳入生产路径 |
| AstrBot 合成文档导入、检索、清理 | 通过 |

验证输出位于 `E:/DockerRes/NyaaNews/validation-rss-20261026/`；这批文件是验证产物，不等同于产品运行状态。

## 4. 目标目录

```text
E:/DockerRes/NyaaNews/
  news/YYYY/MM/DD/<topic-id>/<article-id>.md
  state/nyaanews.db
  logs/
  pending/
  raw/                 # 可选，默认只保留单轮；长期保留须配置

容器：
/data/news/
/data/state/
/data/logs/
/data/pending/
/tmp/nyaanews/<run-id>/
```

正式 Markdown 写入外部盘后，再提交状态事务。临时文件使用同一文件系统的临时名并原子替换；任务 finally 删除临时目录。

## 5. 配置设计

### 公共配置

代码提供配置 schema、无个人内容的示例和校验器。公开示例不得包含真实订阅 URL 白名单、API key、AstrBot 凭据、个人主题名称或真实文章。

### 私有配置

`.env/runtime.env` 保存时区、cron、数据根目录、AstrBot 地址和认证引用。`.env/topics.json` 保存 11 个主题和每类 3 篇限额。新增 `.env/sources.json` 保存：

- source_id、feed_url、enabled、topics、weight、language、content_type、max_age；
- 条件请求和 watermark 的本地状态引用；
- 源级超时、重试、退避和抓取策略；
- `allow_full_text`、`requires_browser`、`blocked` 等能力标记。

## 6. 来源适配器

定义统一接口：

```python
class SourceAdapter(Protocol):
    def fetch_candidates(self, topic_id, window) -> list[Candidate]: ...
```

RSS/Atom 适配器必须支持 RSS 2.0、Atom、namespaces、RFC 822/RFC 3339 时间、guid/id、link href、summary、ETag、Last-Modified、源级失败状态和 watermark。


单源失败不等于主题无新闻。主题状态至少包括 `succeeded`、`empty`、`failed`、`deferred`；只有成功轮询且筛选为空才是 `empty`。

## 7. 每日流程

1. 计算前一自然日的本地和 UTC 半开时间窗。
2. 获取运行锁，清理已结束 run 的临时目录。
3. 校验 topics/sources 配置和外部盘挂载；挂载不可用则停止。
4. 按 source_id 轮询 RSS/Atom，使用条件请求和源级退避。
5. 标准化候选，过滤目标日、语言、类型、黑名单和明显无正文页面。
6. URL、canonical、正文哈希去重；跨主题重复只保留一个 primary_topic。
7. 逐篇抓取公开原文，限制重定向、响应大小、超时和公网目标；拒绝内网、回环和云元数据地址。
8. 提取 Markdown，补充页面 meta/time/JSON-LD 发布时间和图注；正文不足则标记 `extraction_incomplete`。
9. 按主题限额预留文章，写入外部盘 Markdown 和状态。
10. 检查待入库队列，执行 AstrBot 备份、导入、任务轮询和远端对账。
11. finally 清理 `/tmp/nyaanews/<run-id>/`，保留正式文件、状态、日志和失败记录。

## 8. 去重与状态

SQLite 作为事务状态权威，Markdown 作为内容副本。核心表：runs、sources、candidates、articles、daily_allocations、outbox、import_attempts。

文章状态：`discovered → extracted → validated → deduplicated → md_ready → import_pending → submitted → verified`；失败状态包括 `deferred`、`rejected`、`failed`、`unknown`。

URL 去追踪参数但保留有意义查询参数；canonical 需经过可信重定向校验。正文 SHA-256 用于确定性去重；标题相似只产生疑似重复，不直接删除。

修改 SQLite 前必须先创建并校验备份。备份失败停止写入。

## 9. Markdown 契约

```yaml
schema_version: 1
article_id: <stable-id>
primary_topic: <topic-id>
tags: []
source_url: <rss-url-target>
canonical_url: <validated-url>
source_name: <domain>
published_at: <value-or-null>
published_at_raw: <raw-value>
published_precision: <second-day-or-unknown>
feed_seen_at: <timestamp>
fetched_at: <timestamp>
content_sha256: <sha256>
```

正文保留原语言和结构，不把 RSS summary 当作正文，不用模型补写缺失文章。

## 10. AstrBot 同步

使用管理员 JWT，按配置名称唯一解析目标知识库，写入前完成外部备份。采用 `/api/v1/knowledge-bases/{kb_id}/documents/import` 预切块导入。

收到 task_id 只记 `submitted`；轮询到 completed 后核对 success_count、failed_count、uploaded，并读取文档和文本块确认身份与内容版本。请求超时或状态不明时标记 `unknown`，先对账再重试。

验证过的合成资料流程为：备份 → 导入 → 2 块核对 → 语义检索 → 删除测试文档 → 数量恢复。真实新闻批量同步仍需 P4 验收。

## 11. Docker 与调度

- 本机 Docker 容器内运行 Python 和 cron；不部署 NyaaNews 到 macmini 或其他 SSH 生产实例。Windows 主机负责构建并运行本机容器。
- `/data` bind mount 到 `E:/DockerRes/NyaaNews`；配置只读挂载，代码镜像不含 `.env`、`.ref`、新闻、数据库和日志。
- cron 每天北京时间 08:00 触发；容器 timezone 与 Python zoneinfo 必须一致并实测。
- 正式新闻、状态、日志和待重试记录不写容器层。
- 本项目只需要本机 Dockerfile、docker-compose.yml 和本机启动/停止命令；不创建 macmini 专用 restart.py，不执行远程镜像发布。
- 本项目没有远程迁移阶段；仍需检查本机 `E:/DockerRes/NyaaNews/` 目录可用性和数据库备份。

## 12. 阶段计划

| 阶段 | 状态 | 交付与验收 |
| --- | --- | --- |
| P1 基础工程与配置 | ✅ | Python CLI、配置 schema、私有配置读取、依赖锁、数据目录、秘密检查；离线配置测试通过 |
| P2 RSS/Atom 来源层 | 🟡 | 11 源注册、RSS/Atom 解析、条件请求、watermark、源健康和目标日过滤；合成 feed + 真实订阅回归通过 |
| P3 原文归档与每日批处理 | ⬜ | 抓取安全边界、正文提取、图注、时间核验、去重、限额、SQLite 状态、外部盘落盘和临时清理；真实 33 篇批处理通过 |
| P4 AstrBot 同步 | ⬜ | 备份辅助、JWT、目标库、预切块、异步任务、检索和 unknown 对账；专用测试资料闭环通过 |
| P5 Docker 与本机运行 | ⬜ | Dockerfile、compose、cron、timezone、bind mount、日志、恢复和本机容器验收；不部署到 macmini |

每个阶段独立计划、实现、验证、diff/status 检查和交接记录；未完成项不得标记为 ✅。

## 13. V1 验收标准

- 连续 3 个运行日完成调度且不把单源失败误报为空。
- 每轮可追溯候选来源、原文 URL、发布时间和正文哈希。
- 11 个主题的每日限额和跨主题去重稳定。
- 正式文档只出现在外部盘，容器临时目录在任务后为空或仅有当前运行目录。
- AstrBot 导入成功、部分失败和未知结果均可恢复。
- Git 检查确认无 `.env`、`.ref`、新闻正文、数据库、日志和密钥。

## 14. 当前状态

RSS/Atom 33 篇来源与下载原型验证已完成（未验证目标日与主题匹配）；正式产品代码、sources.json、数据库、Docker、cron 和 AstrBot 生产同步尚未实现。
