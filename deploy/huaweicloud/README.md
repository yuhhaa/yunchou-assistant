# 华为云部署接入

此目录提供使用外部 PostgreSQL 和 Redis 的部署配置，可接入华为云 ECS、RDS for PostgreSQL、DCS Redis。它是可执行的连接配置，不是已创建云资源或已经上云的证明。实际资源地址、网络、凭据、TLS和费用由队伍在真实云账号中确认。

## 部署前准备

1. Linux部署主机安装Docker及Compose，放置本项目源码。准备同一VPC内可访问的RDS PostgreSQL和DCS Redis连接地址。
2. 将本目录 `.env.example` 复制为 `.env`，在主机本地填写真实值。三个签名/工具密钥分别使用独立随机字符串，至少32字符。不要复用示例、测试值或登录密码。
3. `DATABASE_URL` 使用 `postgresql+psycopg://用户:URL编码密码@实际主机:端口/库名`；SSL模式及证书参数按实际RDS配置设置。`REDIS_URL` 使用实际 `redis://` 或TLS `rediss://` URL。只开放应用所需的VPC访问，不将数据服务端口直接提供给前端。
4. 真实AI使用 `AGENT_MODE=deepseek` 并填写模型Key、URL与开通的model；地图填写高德Web服务Key。缺Key可先使用local模式验证模拟业务，但不能声称真实AI/道路已通过。
5. 同源反向代理可保持 `API_HOST=127.0.0.1`；若接入ELB私网目标，按实际网络将API绑定到可达地址，并由安全组限制来源。HTTPS证书、域名、ELB或主机反向代理由实际部署负责人配置，本文不提供虚构公网地址。

## 启动与升级

在项目根目录执行：

```bash
docker compose -f deploy/huaweicloud/compose.yaml --env-file deploy/huaweicloud/.env config --quiet
docker compose -f deploy/huaweicloud/compose.yaml --env-file deploy/huaweicloud/.env up -d --build --wait
```

升级已有环境前先备份数据库，并停止旧业务进程。迁移容器先执行增量迁移，新API、计算Worker、聊天Worker和维护器随后启动。不要向已有业务库重复seed，不要通过重建库绕过迁移。

仅当新库需要显式演示数据时执行：

```bash
docker compose -f deploy/huaweicloud/compose.yaml --env-file deploy/huaweicloud/.env exec -T api python -m app.seed
```

实际业务数据导入和真实地图点位确认属于独立步骤。保留 `CLOCK_MODE=SIMULATION` 时仍为仿真业务时钟，真实模型调用与真实地图来源不会把历史仿真订单自动变成真实车队任务。

## 连接验收与证据

分别验证API就绪、数据库迁移、Worker投递、DeepSeek真实调用及真实道路查询。就绪检查不会主动向收费的外部模型/地图发请求。不得把一次READY当作全部外部服务验收。

记录实际资源ID、区域、部署版本、RDS/DCS接入状态、去密钥的调用结果与时间、演示视频和复现步骤。若赛题要求使用CodeArts开发，应保留实际开发/测试任务记录；此仓库不会伪造CodeArts使用记录或云控制台截图。比赛合规以本届官方条款及实际证据为准。
