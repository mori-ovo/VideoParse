# VideoParse

VideoParse 是一个面向自部署场景的视频解析服务。

它的目标不是做“全网万能下载站”，而是尽可能把常见视频页面转换成可直接使用的媒体地址；如果源站只提供音视频分离流，后端会在必要时下载并合并，再对外生成稳定的项目链接。

## 特性

- 支持常见视频站点解析
- 优先返回可直接使用的媒体地址
- 对分离流自动下载并通过 `ffmpeg` 合并
- 提供稳定的项目域名短链，例如 `/api/v1/files/xxxxxxxxxxxx.mp4`
- 内置代理转发，适合外部播放器或只接受视频 URL 的场景
- 支持 Telegram Bot 收视频后返回项目短链
- 支持缓存、输出文件和索引的定时清理

## 当前支持

URL 解析：

- Bilibili
- 抖音
- Twitter / X
- YouTube
- Reddit
- Iwara

附加输入方式：

- Telegram Bot 转发或发送视频文件

## 工作方式

默认模式为 `auto`：

1. 提交一个视频页面链接。
2. 后端先提取媒体信息。
3. 如果源站存在可直接使用的单文件媒体地址，直接返回。
4. 如果只有音视频分离流，后端下载并合并为单个文件。
5. 最终返回项目自己的可访问链接或下载地址。

这个策略比“无条件全量下载”更适合小规格服务器，也更适合拿链接转发给外部播放器。

## 技术栈

- Backend: FastAPI
- Frontend: Vue 3 + Vite + Pinia
- Media extraction: `yt-dlp`
- Media merge: `ffmpeg`
- HTTP client: `httpx`

## 目录结构

```text
VideoParse/
├─ backend/
│  ├─ app/
│  │  ├─ api/
│  │  ├─ core/
│  │  ├─ schemas/
│  │  ├─ services/
│  │  └─ utils/
│  ├─ .env.example
│  ├─ main.py
│  └─ requirements.txt
├─ frontend/
│  ├─ src/
│  ├─ package.json
│  └─ vite.config.ts
├─ cache/
├─ output/
├─ temp/
├─ deploy/
└─ docs/
```

## 环境要求

- Python 3.10+
- Node.js 18+
- `ffmpeg`
- `yt-dlp`

## 快速开始

### 1. 启动后端

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --host 0.0.0.0 --port 8000
```

Windows PowerShell 示例：

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

### 2. 启动前端

```bash
cd frontend
npm install
npm run dev
```

### 3. 构建前端

```bash
cd frontend
npm run build
```

## 最小配置

建议至少配置以下项：

```env
APP_NAME=VideoParse API
DEBUG=false

FRONTEND_ORIGIN=https://your-domain
API_PUBLIC_ORIGIN=https://your-domain

CLEANUP_INTERVAL_HOURS=4
CLEANUP_RETENTION_HOURS=4

DOWNLOAD_FORMAT=bestvideo*[height<=1080]+bestaudio/best[height<=1080]/best
MERGE_OUTPUT_FORMAT=mp4
```

完整变量见 [`backend/.env.example`](backend/.env.example)。

## 平台相关配置

部分平台对登录态、Cookie 或代理比较敏感，生产环境通常需要按平台单独配置。

### Bilibili

常见可选项：

```env
BILIBILI_PROXY=
BILIBILI_SESSDATA=
BILIBILI_BILI_JCT=
BILIBILI_DEDEUSERID=
```

### YouTube

常见可选项：

```env
YOUTUBE_COOKIES=
YOUTUBE_PLAYER_CLIENT=
YOUTUBE_PO_TOKEN=
```

### Twitter / X

常见可选项：

```env
TWITTER_AUTH_TOKEN=
TWITTER_CT0=
TWITTER_COOKIES=
```

### Iwara

常见可选项：

```env
IWARA_AUTHORIZATION=
IWARA_COOKIES=
IWARA_USER_AGENT=
```

## Telegram Bot 集成

项目支持通过 Telegram Bot 接收视频并返回项目短链。

典型流程：

1. 发送或转发视频给 Bot
2. 后端记录 Telegram 文件信息
3. Bot 回复项目域名下的短链
4. 外部播放器直接访问该短链

相关配置：

```env
TELEGRAM_BOT_TOKEN=
TELEGRAM_BOT_API_BASE=http://127.0.0.1:8081
TELEGRAM_POLLING_ENABLED=true
TELEGRAM_POLL_TIMEOUT_SECONDS=20
TELEGRAM_POLL_INTERVAL_SECONDS=2
TELEGRAM_FILE_TIMEOUT_SECONDS=180
TELEGRAM_FILE_PREFETCH_ENABLED=false
TELEGRAM_LOCAL_FILE_SOURCE_PREFIX=
TELEGRAM_LOCAL_FILE_TARGET_PREFIX=
TELEGRAM_ALLOWED_CHAT_IDS=
```

说明：

- `TELEGRAM_BOT_API_BASE` 默认为本地 Bot API
- 如果 `telegram-bot-api` 跑在 Docker 中，而后端跑在宿主机上，需要配置本地文件路径映射
- 生产环境建议限制 `TELEGRAM_ALLOWED_CHAT_IDS`

## API 概览

基础接口：

- `GET /api/v1/health`
- `GET /api/v1/history`

解析接口：

- `POST /api/v1/parse`
- `GET /api/v1/tasks/{task_id}`
- `GET /api/v1/tasks/{task_id}/result`
- `GET /api/v1/tasks/{task_id}/redirect?kind=single|video|audio`
- `GET /api/v1/tasks/{task_id}/proxy?kind=single|video|audio`

文件接口：

- `GET /api/v1/files/{file_id}/download`
- `GET /api/v1/files/{file_id}.{ext}`
- `GET /api/v1/files/{file_id}/{file_name}`

示例请求：

```http
POST /api/v1/parse
Content-Type: application/json

{
  "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "delivery_mode": "auto"
}
```

## 返回结果

后端当前有三种结果类型：

- `direct`: 直接可用的媒体地址
- `split_streams`: 音视频分离流
- `download`: 后端已生成可访问的成品文件

在默认的 `auto` 模式下，接口会尽量把分离流进一步处理成更稳定的单文件结果。

## 部署建议

- 前后端建议放在同一域名下，通过反向代理转发 `/api/`
- 对外返回的地址应与 `API_PUBLIC_ORIGIN` 保持一致
- `cache/`、`temp/`、`output/` 需要可写权限
- 如果使用 Telegram 本地 Bot API，建议与后端部署在同一台机器

## 限制说明

- 本项目不以支持所有站点为目标
- 某些平台依赖登录态、Cookie、代理或地区网络环境
- 受保护内容、私有资源或 DRM 内容不在主要支持范围内
- 第三方站点结构变更后，解析规则可能需要同步调整

## 开发状态

当前版本已经可以用于日常自部署使用，重点放在：

- 常见站点的视频解析
- 单文件结果优先
- 小规格服务器可承受的资源消耗
- Telegram Bot 短链分发

后续更适合继续完善的方向：

- 回归测试
- 任务持久化
- 更细的日志和监控
- 新平台适配器的标准化接入
