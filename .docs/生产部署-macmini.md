# NyaaNews 生产部署（macmini）

2026-11-01 用户决定从 Windows 迁移到 macmini，旧“只本机运行”决定不再适用。
生产采集任务只运行一份；Windows 留作开发、构建和推送，不重新启动本机采集容器。

## 文件和目录

| 位置 | 用途 |
| --- | --- |
| /root/DockerContainer/NyaaNews | 生产 Compose、restart.py、私有 .env/ |
| /root/DockerContainer/DockerRes/NyaaNews | 正式文章、状态、日志，绑定 /data |
| /root/DockerContainer/DockerRes/backups/NyaaNews/deployments/database | 运行数据库备份，独立绑定 /data/backups |
| DockerRes/backups/NyaaNews/deployments/<迁移事件> | 初始迁移归档、配置与 SHA-256 清单 |
| E:/DockerRes/NyaaNews | 停机时的本地副本，保留回退用途，不再作为生产状态 |

生产文件使用 docker-compose.publish.yml（传输到生产可命名为 docker-compose.yml），其中只引用配置中的私有镜像，不包含 build。
数据绑定使用 create_host_path=false，restart.py 先确认 STORAGE_MOUNT_ROOT 真正挂载，并确认数据与备份目录在该挂载盘内。
只读配置仍使用 .env/ 目录和 runtime.env 文件，避免与已有私有配置结构冲突。

## 配置

开发机与生产机各自持有 .env/runtime.env，不能整份互相覆盖。
开发机 PRIVATE_DOCKER_REGISTRY_HOST/URL 用构建推送地址；生产机用该主机可达的拉取地址。真实值不入版本管理。
生产的 DATA_DIR 为 /data，HOST_DATA_DIR、HOST_BACKUP_DIR 和 STORAGE_MOUNT_ROOT 使用上表对应位置。
主题、来源和已认证的 SSH known_hosts 保持迁移时内容；专用 SSH key 只供受限知识库网关使用。
生产配置清除 GATEWAY_SOCKS_HOST，避免依赖 Windows 代理；历史 LLM 字段不传入生产。

## 构建发布

在开发机执行：

```console
python rebuild.py
```

脚本只构建/推送，不启动容器。使用当前 Git 短 SHA 和 latest 双标签，推送最多尝试三次。
注册表清理前解析两个保留标签的 digest；共用该 digest 的旧标签跳过，保护 digest 无法确定则取消清理。
所有命令输出中的注册表地址掩码。--skip-push 仅本地构建，--skip-cleanup 保留旧镜像清单用于维护。

## 生产启动更新

将生产 Compose、restart.py 和私有配置放入生产部署目录，确认大储存挂载后执行：

```console
python3 restart.py
```

顺序：存储预检 → Compose 校验 → pull → down → up → 最长180秒健康核验 → 本项目标签范围的悬空镜像清理。
拉取失败时不停止已有服务；启动或健康检查失败会返回错误，不报告部署成功。脚本自动补齐 Snap Docker 的 PATH。
--no-pull 只使用已有镜像，--skip-cleanup 保留悬空镜像。维护前仍应根据实际变更备份状态与配置；本脚本不自动执行完整应用回滚。

## 迁移数据和验证

先停源端，再打包全部业务文件、状态及历史备份。传输归档只能落到目标挂载盘备份目录；校验归档摘要，解包后逐文件核验大小与 SHA-256，再对 SQLite 做 quick_check。
历史数据库备份转入统一备份目录；旧 scheduler/container 标记保存在迁移包中，活动目录重新建立新主机记录。
启动后核对文章/队列数量、本地文件哈希、远端已入库文档、定时时区与 cron、真实订阅和正文请求、SSH知识库连接。
不得为了验证清空已有 verified 状态或重复导入历史资料。保持每天北京时间08:00及前一自然日规则。
临时传输文件清理，完整迁移恢复包和本地副本保留。日常备份保留策略不随迁移自动改变。

## 回退边界

需要回退时先停止生产采集器，核实迁移后新增业务写入，再恢复匹配的镜像、配置和最新状态。
不能直接启动旧 Windows 副本与生产同时工作，也不能用旧数据库覆盖生产之后的新闻和导入记录。
历史本机验收是过去状态，实际迁移结果以最新交接与 SSOT 为准。
