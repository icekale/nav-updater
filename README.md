# 投研净值更新工具

这是第一阶段的局域网 MVP：上传原始净值 Excel 和高清截图，预览产品匹配，自动查询已登记的公募产品，生成新的 Excel，并保留历史批次。

## 部署

部署主机需要 Docker Engine 和 Docker Compose v2。

```bash
cp .env.example .env
openssl rand -hex 32
# 将随机值写入 .env 的 SESSION_SECRET，并修改初始管理员密码
docker compose up -d --build
docker compose ps
```

在浏览器打开 `http://<部署主机局域网IP>:8080`。初始管理员由 `.env` 中的 `INITIAL_ADMIN_USERNAME` 和 `INITIAL_ADMIN_PASSWORD` 创建；首次登录后应修改密码或创建新管理员。

`app` 启动时执行 `alembic upgrade head`，`worker` 等待数据库后直接处理队列，避免多个容器同时迁移。PostgreSQL、上传文件和输出文件分别保存在 Compose 持久卷，容器重启不会清空历史。

## 第一次使用

1. 管理员进入“产品目录”，导入包含 `product_name,product_code,product_type` 三列的 CSV；`product_type` 只能是 `public` 或 `private`。
2. 进入“新建更新”，上传 `.xlsx` 模板和一张或多张 PNG/JPEG 截图。
3. 确认截止日期，查看匹配预览；需要时跳过待确认条目。
4. 点击“开始处理”，下载生成的新 Excel；原始文件不会被覆盖。

第一阶段默认使用东方财富公开基金净值接口，按基金代码获取累计净值。私募/渠道产品如果没有截图匹配，会保留原值并标红；系统不会自动登录或绕过私募平台权限。

## 运维

```bash
docker compose logs -f app worker
docker compose exec db pg_isready -U nav -d nav
scripts/backup.sh ./backups
scripts/restore.sh ./backups
docker compose down
```

备份脚本同时保存 PostgreSQL dump 和应用文件卷。恢复前会停止 `app`/`worker`，恢复数据库和文件卷后再启动服务。不要把 `.env`、备份文件或产品截图提交到 Git。

## 当前边界

会议跟踪、AI 分析、人工审核工作台和微信提醒属于第二阶段，不会在第一阶段自动出现。第一阶段支持局域网多人账号和共享历史记录，不提供公网 HTTPS 或自助注册。
