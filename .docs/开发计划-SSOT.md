# 猫猫报纸（NyaaNews）开发计划 SSOT

版本：V1 / 0.1.0。更新日期：2026-10-26。
用户已批准自动实现及本机 Docker 部署。本文为开发状态的唯一事实来源。

## 目标与边界

- RSS/Atom 发现候选，直接读取公开原文；每篇保存独立 Markdown，保持原语言，不翻译、不摘要。
- 北京时间每天 08:00 采集前一自然日，使用本地日期到 UTC 的半开时间窗。
- 当前私有配置有 11 个主题，每类最多 3 篇；不够时留空，不以旧文或无关文补数。
- 标题、正文、原文 URL、canonical URL、发布时间、订阅时间和抓取时间可追溯。
- 正式数据位于 E:/DockerRes/NyaaNews，通过 bind mount 映射到容器 /data。
- 仅在本机 Docker Desktop 部署，不在 macmini 部署新闻采集器，不发布远程镜像。
- 外部既有 AstrBot 接收去重后的文档；使用专用受限 SSH 网关调用其 API。
- 无 LLM、dsh、新闻 API 或 MCP 运行依赖。

## 私有配置与目录

.env/runtime.env 保存时区、cron、数据目录、AstrBot 凭据、目标库和 SSH 配置。
.env/topics.json 保存主题及额度；.env/sources.json 保存真实订阅 URL、主题映射、权重、匹配正则和来源策略。
配置路径相对 runtime.env 目录解析；只有 DATA_DIR、NYAANEWS_CONFIG、REQUIRE_MOUNT 接受环境覆盖。
公开 config/ 仅含通用示例。历史验证资料原样保存在被忽略的 .ref/。
api.txt 已迁移并删除；历史模型配置保存在私有配置中，生产程序不读取。

```text
/data/news/YYYY/MM/DD/<topic-id>/<article-id>.md
/data/state/nyaanews.db
/data/state/last-run.json
/data/state/scheduler.json
/data/logs/<day>-<run-id>.json
/data/backups/                         # 写事务前校验的数据库快照
/data/pending/                         # 保留的运维目录；队列权威在 SQLite
/tmp/nyaanews/run-*/                    # 单轮临时目录，结束后清理
```

.env、.ref、新闻、数据库、日志、虚拟环境均不进 Git 或镜像。
Dockerfile 只复制锁定依赖、src 和容器调度入口。配置只读挂载，/tmp 使用 tmpfs。

## 已确认架构与实现契约

Python 3.12 + feedparser + urllib3 + Trafilatura + BeautifulSoup + SQLite + filelock。
requirements.lock 锁定版本及哈希。rebuild.py 只构建本机镜像，compose 负责运行。

1. 持有数据目录运行锁后，恢复通过哈希校验的孤立归档，并清理上轮临时目录。
2. 轮询启用来源；支持 RSS/Atom、alternate 链接、ETag、Last-Modified、304 缓存、watermark 和失败退避。更换订阅地址时重置旧地址的缓存与退避。
3. 按原始发布时间、来源主题映射和 include/exclude 正则筛选。社区提交时间不能替代原文发布时间，时区不明或日期不足以确定时间窗时不伪造。
4. 逐跳验证公网 DNS 与重定向，连接固定的公网 IP，TLS 校验原主机，限制响应大小和时间，拒绝内网目标及 HTTPS 降级。
5. 提取原文和图注，使用文章标题而非站点 logo；正文不足则保留失败记录。
6. 对规范化 URL、canonical 与正文 SHA-256 去重。跨主题同篇只占一个主主题，重跑沿用每日额度。
7. Markdown 原子落盘后提交状态事务；每次 SQLite 写事务前在线备份并 quick_check，备份失败则拒绝写入。
8. 持久 outbox 记录导入尝试。提交前标记 submitted；超时先对账，不盲重传。
9. AstrBot 每次导入或删除前在其独立挂载盘备份，再预切块导入、轮询任务、逐块核对。
10. 单源或单篇失败独立记录；partial 不能冒充全部成功。任务结束清理临时产物。

SQLite 表为 runs、sources、candidates、articles、daily_allocations、outbox、import_attempts。
主题结果包含 succeeded、empty、partial、deferred、failed；来源请求失败不能记为主题 empty。
标题相似不作为自动删除条件。正文改变但同 URL 的版本修订需人工维护，V1 不自动覆盖历史。

## Markdown 与同步契约

Frontmatter 包含 schema_version、article_id、title、primary_topic、tags、target_date、source_url、canonical_url、source_name、source_id、source_feed、language、published_at、published_at_raw、published_precision、publication_evidence、feed_seen_at、fetched_at、content_sha256。
正文包含原标题、来源/时间索引和 Article 原文。正文哈希与元信息一致后才允许入库。

SSH 专用密钥使用 forced-command，入口只允许目标知识库及本项目文档的必要操作；任务轮询限制到网关创建的任务，语义检索固定目标库。
网关返回服务端备份回执；本地快照不能替代 AstrBot 备份。
备份保证单个 SQLite 文件一致性，不声称已实现跨索引原子恢复或完成恢复演练。
可配置 SOCKS5 仅作为 SSH 传输；严格的目标主机密钥校验保持启用。

## 阶段状态

| 阶段 | 状态 | 实现与验收 |
| --- | --- | --- |
| P1 基础工程与配置 | ✅ | 私有配置、依赖锁、目录检查、Git 与许可证；配置及自然日窗口测试通过 |
| P2 RSS/Atom 来源层 | ✅ | 解析、缓存、退避、来源隔离；合成 RSS/Atom、304、429、公网边界测试及 16 个真实源轮询通过 |
| P3 原文归档与批处理 | ✅ | 原文、日期、标题、哈希、配额、恢复和外部盘归档；模拟 3 日共 99 篇、重跑 0 新增及真实归档通过 |
| P4 AstrBot 同步 | 🟡 | 实现和真实验证已进行，待阶段收尾 |
| P5 Docker 与本机运行 | ⬜ | 已启动本机容器，待最终部署核验与收尾 |

阶段验证后按显式文件清单提交，交接记录见 .docs/阶段交接-XXX.md。

## 验证事实与验收口径

- 历史 33 篇原型验证仅证明下载和落盘，没有严格核验目标日与主题，不能替代产品验收。
- 2026-10-26 自动化测试覆盖连续 3 个模拟日期、每天 11 主题各 3 篇，共 99 篇；重跑均新增 0 篇。临时测试目录随测试清理。
- 正式采集目标日为 2026-10-25，当前归档并核对入库 7 篇。其余主题按实际结果留空；1 个候选正文请求失败，保留 deferred，不补数。
- 本机容器内完成真实来源轮询、原文落盘、SSH/API 同步及重复运行验证。
- 已实测由 cron 触发完整 tick，重跑新增 0 篇；调度探针与日志已清理，正式计划保持每天 08:00。
- 合成知识库资料完成导入、任务轮询、逐块核验、语义检索及删除；目标库文档数量恢复。
- 标题修正前均备份，3 篇修订后重新导入并核对；不删除其他用户资料。

## 持续运行验收与已知边界

连续 3 个真实运行日的定时观测尚未满期。模拟 3 日测试与单次真实 cron 通过不能替代这一时间条件；开发部署完成后仍需检查后续每日记录。
宿主机和 Docker Desktop 必须在调度时运行；V1 不自动补采关机期间漏掉的日期，可用 run --date 手动补采。
本站点反爬、动态正文与发布时间缺失可能造成真实缺口，见逐轮日志，不用模型补写。
当前 Docker 到局域网直连超时，SSH 使用宿主机既有 SOCKS 服务；该服务不可用时同步会失败并留下状态。
备份和历史运行记录不自动删除，容量保留策略需另行明确；只自动清理临时工作目录。
