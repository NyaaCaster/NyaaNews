# 猫猫报纸 / NyaaNews

使用简体中文交流。开发状态以 .docs/开发计划-SSOT.md 为准。

- 生产采集器部署于 macmini；本机只开发、构建和推送镜像，未经要求不启动本机容器。新闻来源只使用 RSS/Atom，不依赖 LLM/MCP。
- .env/ 与 .ref/ 是私有资料，禁止提交或复制到镜像。真实主题、来源、凭据只从配置读取。
- 生产运行数据放在 /root/DockerContainer/DockerRes/NyaaNews，容器映射 /data；数据库备份绑定到挂载盘 backups/NyaaNews/deployments/database。E:/DockerRes/NyaaNews 保留迁移前副本。临时工作目录须在退出后清理。
- 数据库写入前先做一致性备份；仅清理本次创建的测试数据，保护用户文档与备份。
- 阶段完成需验证、检查 diff/status、更新 SSOT 与交接，并按本次自动开发授权提交推送。
- GitHub 操作用进程 GITHUB_PAT，不能持久化或输出令牌。
- 测试：python -m unittest discover -s tests -v。依赖使用 requirements.lock。
- 本机 python rebuild.py 构建并推送私有仓库，不启动容器；--skip-push 仅本地构建。生产使用 docker-compose.publish.yml 和 restart.py，部署前核验大存储挂载。私有仓库地址只从 .env/runtime.env 读取并掩码输出。
