# VideoParse

一个面向少量内部使用场景的万能视频解析项目，采用前后端分离结构。

当前目标不是“做一个大规模下载站”，而是“优先拿到可用直链；如果源站只有音视频分离流，就自动下载并合成为单文件，再给出项目自己的可访问地址”，方便转发到 `VRChat` 之类只认视频 URL 的场景。

支持平台：

- `Bilibili`
- `抖音`
- `Twitter / X`
- `YouTube`
- `Reddit`

## 核心策略

当前默认流程已经改为 `auto`：

1. 用户提交视频页面链接。
2. 后端先用 `yt-dlp` 提取媒体信息。
3. 如果源站本身就有单文件直链，直接返回可复制地址。
4. 如果源站只有音视频分离流，后端自动下载并通过 `ffmpeg` 合流。
5. 最终前端只保留两个主要动作：
   - `复制直链`
   - `下载视频`

这套策略比“无脑全量下载”更适合 `1C1G Ubuntu` 小服务器，也更符合“拿链接转发”的项目目标。

## 当前项目结构

```text
VideoParse/
├─ frontend/                    # Vue 3 + Vite 前端
│  ├─ src/
│  │  ├─ api/
│  │  ├─ components/
│  │  ├─ pages/
│  │  ├─ stores/
│  │  └─ types/
│  ├─ package.json
│  └─ vite.config.ts
├─ backend/                     # FastAPI 后端
│  ├─ app/
│  │  ├─ api/
│  │  ├─ core/
│  │  ├─ schemas/
│  │  ├─ services/
│  │  └─ utils/
│  ├─ .env.example
│  ├─ main.py
│  └─ requirements.txt
├─ cache/                       # yt-dlp / 代理缓存
├─ temp/                        # 临时下载目录
├─ output/                      # 合流后的最终文件
├─ docs/
└─ readme.md
```

## 结果类型说明

后端仍然保留 3 种结果类型，但前端已经按统一流程整合：

### `direct`

表示拿到了单文件媒体直链。

常见字段：

- `direct_url`
- `redirect_url`
- `proxy_url`

### `split_streams`

表示源站只提供分离流。

常见字段：

- `video_url`
- `audio_url`
- `video_proxy_url`
- `audio_proxy_url`

注意：在当前默认 `auto` 模式下，这种情况通常不会直接停在这里，而是会继续进入自动下载和合流。

### `download`

表示后端已经把最终单文件视频准备好，并返回项目自己的下载地址。

常见字段：

- `file_id`
- `download_url`

## 为什么 B 站经常拿不到“原始单文件直链”

这不是项目单独造成的，而是很多 B 站视频本来就是音视频分离流。

也就是说：

- 你可以解析成功
- 也可以拿到视频流和音频流
- 但源站本身未必提供一个天然的单文件 URL

所以当前项目的处理方式是：

- 优先尝试单文件直链
- 如果没有，就自动下载分离流
- 使用 `ffmpeg` 合并成单文件
- 再返回本项目自己的可访问地址

## 平台登录态与代理策略

### Bilibili

建议优先配置：

```env
BILIBILI_PROXY=socks5://127.0.0.1:1080
BILIBILI_SESSDATA=你的 SESSDATA
BILIBILI_BILI_JCT=你的 bili_jct
BILIBILI_DEDEUSERID=你的 DedeUserID
```

说明：

- B 站对服务器 IP 风控较敏感，`412` 很常见。
- 只在 `Bilibili` 上单独走 `SOCKS5`，比全站代理更省资源。
- 如果只有 `SESSDATA`，也可以先试；但完整登录态通常更稳。

### YouTube

建议配置完整 Cookie 字符串：

```env
YOUTUBE_COOKIES=完整的 YouTube Cookie 串
```

说明：

- YouTube 的登录校验比较严格。
- 手动只填一两个字段往往不稳定。
- 当前项目仍支持旧的文件方式和旧变量名，但主模板已经不再强调它们。

### Twitter / X

优先使用最简配置：

```env
TWITTER_AUTH_TOKEN=你的 auth_token
TWITTER_CT0=你的 ct0
```

说明：

- 你的很多场景只靠 `auth_token` 也可能成功。
- 但如果推文涉及敏感内容、登录可见内容，`ct0` 一并配置通常更稳。
- 如果你已经拿到了完整 Cookie 串，也可以填：

```env
TWITTER_COOKIES=auth_token=...; ct0=...
```

## 推荐的 `.env` 最小配置

生产环境示例：

```env
APP_NAME=VideoParse API
DEBUG=false

FRONTEND_ORIGIN=https://moriparse.space
API_PUBLIC_ORIGIN=https://moriparse.space

CLEANUP_INTERVAL_HOURS=6
CLEANUP_RETENTION_HOURS=6

USER_AGENT=
PROXY=

BILIBILI_PROXY=socks5://127.0.0.1:1080
BILIBILI_SESSDATA=
BILIBILI_BILI_JCT=
BILIBILI_DEDEUSERID=

YOUTUBE_COOKIES=

TWITTER_AUTH_TOKEN=
TWITTER_CT0=

DOWNLOAD_FORMAT=bestvideo*[height<=1080]+bestaudio/best[height<=1080]/best
MERGE_OUTPUT_FORMAT=mp4
```

说明：

- `FRONTEND_ORIGIN` 和 `API_PUBLIC_ORIGIN` 在你走反向代理到同一主域名时，通常都不需要再带 `:5173` / `:8000`。
- 只要外部用户最终访问的是 `https://moriparse.space`，这里就应该写这个公开地址。
- 端口只用于本机服务监听，不应该继续出现在对外返回的下载地址里。

## 1C1G 服务器优化思路

当前默认实现已经按小机器做了取舍：

- 默认只在必要时下载，不再一上来就跑全量下载。
- 默认下载格式限制到 `1080p`，避免高码率素材把 CPU、磁盘和带宽打满。
- 只有需要单文件成品时才走 `ffmpeg` 合流。
- 缓存和临时目录每 `6` 小时自动清理一次。
- 任务状态只保存在内存中，避免额外引入 Redis / 数据库。

仍需注意：

- `output/` 里的最终文件不会被当前清理器自动删除。
- 如果后续下载型任务多了，建议再补一个 `output/` 生命周期清理策略。

## 常见问题

### 1. 为什么健康检查是正常的，但前端报解析失败？

先检查反向代理是否把 `/api/` 指向了后端，而不是错误地回退到了前端静态页。

正确现象应该是：

```bash
curl https://your-domain/api/v1/health
```

返回 JSON，而不是 `index.html`。

### 2. 为什么 YouTube 报 `Requested format is not available`？

当前后端已经加入格式回退逻辑：

- 先按配置的下载格式尝试
- 如果目标格式不可用，再自动回退到更宽松的格式

同时默认格式已改得更保守，适合小机器。

### 3. 为什么 Twitter 明明有视频，却说 `No video could be found in this tweet`？

常见原因不是“真的没视频”，而是：

- 该推文需要登录后才可见
- 该内容是敏感内容
- 当前 Cookie 不完整
- 当前账号本身没有权限看到完整媒体

优先先试：

```env
TWITTER_AUTH_TOKEN=...
TWITTER_CT0=...
```

### 4. 为什么 Bilibili 会报 `412`？

这通常是平台风控，不是前端问题。

优先检查：

- `BILIBILI_PROXY`
- `BILIBILI_SESSDATA`
- `BILIBILI_BILI_JCT`
- `BILIBILI_DEDEUSERID`

## 接口概览

### 基础接口

- `GET /api/v1/health`
- `GET /api/v1/history`

### 解析相关

- `POST /api/v1/parse`
- `GET /api/v1/tasks/{task_id}`
- `GET /api/v1/tasks/{task_id}/result`
- `GET /api/v1/tasks/{task_id}/redirect?kind=single|video|audio`
- `GET /api/v1/tasks/{task_id}/proxy?kind=single|video|audio`

### 文件下载

- `GET /api/v1/files/{file_id}/download`

## 请求示例

```http
POST /api/v1/parse
Content-Type: application/json

{
  "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "delivery_mode": "auto"
}
```

## 开发运行

### 后端

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

### 前端

```bash
cd frontend
npm install
npm run dev
```

### 生产构建

```bash
cd frontend
npm run build
```

## 当前阶段结论

这个项目现在已经不是纯规划状态，而是一个围绕“直链优先、必要时自动合流”的基础可运行版本。

已完成的方向：

- 已接入真实下载器 `yt-dlp`
- 已支持 `ffmpeg` 自动合流
- 已支持 Bilibili 单独走 `SOCKS5`
- 已支持 YouTube / Twitter / Bilibili 通过 `.env` 注入登录态
- 已把缓存清理固定为每 `6` 小时一次
- 已把前端主要操作收敛为“复制直链”和“下载视频”

下一阶段如果继续做，优先级建议是：

1. 给 `output/` 加生命周期清理
2. 增加任务持久化
3. 根据不同平台补更细的登录态检查和错误提示
