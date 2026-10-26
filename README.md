# 猫猫报纸 · NyaaNews

RSS/Atom 驱动的每日公开新闻原文归档工具，可同步到现有 AstrBot 知识库。
无需大模型、新闻 API 或 MCP。

## 配置与运行

1. 从 config/ 下示例创建私有 .env/runtime.env、topics.json 和 sources.json。
2. 在宿主机创建数据根目录，设置 HOST_DATA_DIR；Windows 数据盘示例为 E:/DockerRes/NyaaNews。
3. 为每个主题配置 source_order，并在来源中配置对应 topic_rules。include/exclude 筛选标题、摘要和 URL；title_include/title_exclude 可约束标题，避免将泛技术站整站归入专业主题。
4. 执行 python rebuild.py 构建镜像。
5. 执行 docker compose --env-file .env/runtime.env up -d 运行。

默认每日 08:00 按配置时区采集前一自然日。没有合格文章的主题允许为空。
新闻、状态、日志和备份均存 /data 的宿主机绑定目录；任务临时目录会被清理。
秘密和历史参考资料不进入镜像或 Git。项目不含个人订阅列表。

## CLI

    python -m nyaanews.cli --config /run/nyaanews-config/runtime.env check
    python -m nyaanews.cli --config /run/nyaanews-config/runtime.env run --date YYYY-MM-DD
    python -m nyaanews.cli --config /run/nyaanews-config/runtime.env sync

run 可用 --no-sync 仅归档；重跑同一天不会重置主题额度。
来源按各主题 source_order 逐个取用，满额即停，缺额才用后续源。共享订阅每轮只轮询一次。
来源耗尽仍不足时按实际数量入库；日志记录已取用/跳过来源、筛选计数和剩余缺额。
退出码 0 为完成，2 为部分失败；具体来源和正文失败原因见 /data/logs。

## AstrBot 接入

同步使用专用 SSH forced-command 网关访问已存在的 AstrBot 服务。
ops/kb_gateway.py 必须由该服务管理员安装，并绑定单个知识库。
导入和删除前网关创建数据库一致性快照；备份失败则拒绝写入。
仅为接入既有服务，不在远端运行新闻采集，不向容器挂载管理密钥或 Docker socket。
新安装默认 SYNC_ENABLED=0，配置完成后再启用。
未知提交结果先对账，不盲目重传。备份不自动清理。

## 开发验证

    uv venv .venv
    uv pip sync requirements.lock
    PYTHONPATH=src python -m unittest discover -s tests -v

实际状态见 .docs/开发计划-SSOT.md，完整接入与维护步骤见 .docs/运维说明.md。
项目整体架构、模块、数据契约及设计方法见 [NyaaNews 猫猫报纸技术文档](.docs/NyaaNews-猫猫报纸.md)。
容器 healthy 表示调度存活；partial 批次的业务失败仍需查看逐轮日志。
此前 33 篇下载验证只证明传输/提取路径，不代表每日主题和日期验收。

MIT License.
