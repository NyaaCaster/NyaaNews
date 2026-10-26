# 猫猫报纸 / NyaaNews

使用简体中文交流。开发状态以 .docs/开发计划-SSOT.md 为准。

- 本项目仅在本机 Docker 部署。新闻来源只使用 RSS/Atom，不依赖 LLM/MCP。
- .env/ 与 .ref/ 是私有资料，禁止提交或复制到镜像。真实主题、来源、凭据只从配置读取。
- 运行数据放在 E:/DockerRes/NyaaNews，容器映射 /data。临时工作目录须在退出后清理。
- 数据库写入前先做一致性备份；仅清理本次创建的测试数据，保护用户文档与备份。
- 阶段完成需验证、检查 diff/status、更新 SSOT 与交接，并按本次自动开发授权提交推送。
- GitHub 操作用进程 GITHUB_PAT，不能持久化或输出令牌。
- 测试：python -m unittest discover -s tests -v。依赖使用 requirements.lock。
- 本机构建通过 python rebuild.py，脚本只构建；本轮用户已授权本机 Docker 部署。
