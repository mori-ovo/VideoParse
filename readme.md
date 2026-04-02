# VideoParse

一个面向少量内部用户的万能视频解析项目，采用前后端分离结构，目标是优先返回可用媒体地址，而不是默认把所有视频都下载到本地。

当前项目重点场景：

- 解析 `Bilibili`、`Douyin`、`Twitter / X`、`YouTube`、`Reddit`
- 给用户返回可访问的媒体地址
- 优先服务 `VRChat` 这类“需要一个可用 URL”的转发场景
- 部署在 `1C1G Ubuntu` 小规格服务器上，尽量控制 CPU、内存、磁盘占用

## 1. 当前定位

这个项目现在不是“下载优先”的站点，而是“直链优先”的解析服务：

- 默认模式是 `delivery_mode=direct`
- 只有用户显式选择 `download` 时，后端才会执行真实下载
- 如果视频和音频分离，且用户选择了 `download`，后端才会调用 `ffmpeg` 合流
- 如果源站本身就有可播放的单文件媒体流，优先返回项目生成的代理直链

这套策略更适合 `1C1G` 服务器，也更符合“拿地址转发给其他应用使用”的目标。

## 2. 当前已实现能力

- 前后端分离
- 基于 `yt-dlp` 的真实媒体解析
- 支持平台识别：
  - `bilibili`
  - `douyin`
  - `twitter`
  - `youtube`
  - `reddit`
- 返回视频标题、时长、封面、发布者、解析器等基础元数据
- 默认直链模式下返回：
  - 源站原始地址 `direct_url`
  - 项目重定向地址 `redirect_url`
  - 项目代理地址 `proxy_url`
- 对于音视频分离资源，返回：
  - `video_url` / `audio_url`
  - `video_redirect_url` / `audio_redirect_url`
  - `video_proxy_url` / `audio_proxy_url`
- 下载模式下：
  - 使用 `yt-dlp` 下载
  - 如有需要通过 `ffmpeg` 自动合流
  - 返回项目托管的 `download_url`
- 健康检查接口
- 历史任务接口
- `temp/` 与 `cache/` 每 `6` 小时自动清理一次，清理阈值同样是 `6` 小时

## 3. 标准项目结构

```text
VideoParse/
├── frontend/                      # Vue 3 前端
│   ├── src/
│   │   ├── api/
│   │   ├── components/
│   │   ├── pages/
│   │   ├── router/
│   │   ├── stores/
│   │   └── types/
│   ├── index.html
│   ├── package.json
│   └── vite.config.ts
├── backend/                       # FastAPI 后端
│   ├── app/
│   │   ├── adapters/
│   │   ├── api/
│   │   │   └── v1/endpoints/
│   │   ├── core/
│   │   ├── ffmpeg/
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── services/
│   │   ├── storage/
│   │   ├── tasks/
│   │   └── utils/
│   ├── main.py
│   └── requirements.txt
├── cache/                         # yt-dlp / 代理缓存目录
├── temp/                          # 临时下载目录
├── output/                        # 下载模式的最终文件输出目录
├── deploy/
├── docs/
├── scripts/
└── readme.md
```

当前技术栈：

- 前端：`Vue 3`、`Vite`、`TypeScript`、`Pinia`、`Vue Router`、`Axios`
- 后端：`FastAPI`、`Pydantic`、`yt-dlp`、`httpx`
- 媒体处理：`ffmpeg`

## 4. 核心处理流程

### 4.1 直链模式

1. 前端提交视频页面 URL 到 `POST /api/v1/parse`
2. 后端创建任务并异步执行解析
3. 后端通过 `yt-dlp` 提取元数据与媒体流信息
4. 如果存在单文件可播流，返回 `direct` 结果
5. 如果只有分离流，返回 `split_streams` 结果
6. 前端展示可直接使用的项目 URL

### 4.2 下载模式

1. 前端提交 URL，`delivery_mode=download`
2. 后端通过 `yt-dlp` 下载媒体
3. 如果音视频分离且需要合流，调用 `ffmpeg`
4. 后端把最终文件注册到本地存储索引
5. 返回 `download_url`

## 5. 结果类型说明

后端结果分为 3 类：

| `result_type` | 说明 | 关键字段 | 适用场景 |
| --- | --- | --- | --- |
| `direct` | 存在单文件可播放媒体流 | `direct_url`、`redirect_url`、`proxy_url` | 最适合直链转发 |
| `split_streams` | 只有分离的视频流和音频流 | `video_*`、`audio_*` | 源站没有单文件直链 |
| `download` | 已下载并生成本地成品文件 | `file_id`、`download_url` | 必须提供单文件成品时 |

需要特别说明：

- `direct_playable=false` 不等于解析失败
- 它只表示“当前没有单文件可播放 URL”
- 例如 `Bilibili` 很多资源天然只有分离流，这时更常见的是 `split_streams`
- 如果目标播放器必须吃一个单独的视频文件地址，那么应切换到 `download` 模式

## 6. 项目生成直链的含义

你前面提到“能不能换个方法，比如让项目生成直链”，当前项目已经支持两种“项目生成地址”：

### 6.1 `redirect_url`

示例：

```text
/api/v1/tasks/{task_id}/redirect?kind=single
```

特点：

- 后端会重新解析任务对应的源站地址
- 拿到最新媒体地址后返回 `307 redirect`
- 服务端开销较小
- 适合接受重定向的客户端

### 6.2 `proxy_url`

示例：

```text
/api/v1/tasks/{task_id}/proxy?kind=single
```

特点：

- 由项目服务端代理源站媒体流
- 支持 `GET` 和 `HEAD`
- 支持转发 `Range` 请求
- 保留了关键响应头，适合播放器或外部应用读取
- 对 `VRChat` 这类需要稳定访问域名的场景更友好

### 6.3 重要限制

项目可以“生成项目自己的访问地址”，但不能凭空把一个不存在的单文件源站媒体变出来：

- 如果源站只提供分离流，本项目只能返回 `video` / `audio` 代理地址
- 如果业务目标必须得到一个单文件成品 URL，只能走 `download + ffmpeg` 合流
- `direct_url` 往往带时效，不建议直接长期保存
- 生产环境推荐优先使用 `proxy_url` 或 `redirect_url`

## 7. 对 VRChat 的建议

如果目标是把地址转发到 `VRChat`，建议按下面顺序使用：

1. 优先使用 `proxy_url`
2. 如果没有单文件流但目标能接受分离媒体，再尝试 `video_proxy_url` / `audio_proxy_url`
3. 如果目标必须是一个单独文件，则使用 `download` 模式拿 `download_url`

说明：

- `proxy_url` 最稳定，但会消耗你服务器自己的出站带宽
- `redirect_url` 更省资源，但依赖客户端是否接受重定向，以及源站链接是否及时刷新
- 对 `1C1G` 机器来说，长期高并发代理并不合适，这个项目更适合少量内部使用

## 8. 1C1G Ubuntu 服务器优化策略

当前实现已经按小机器思路做了取舍：

- 默认 `direct` 模式，避免无意义下载
- 下载格式限制为：

```text
best[height<=1080]/bestvideo*[height<=1080]+bestaudio/best
```

- 代理采用流式转发，不整文件读入内存
- 默认分块大小为 `65536`
- 默认代理连接上限为 `20`
- 任务状态当前保存在内存里，减少 Redis / 数据库依赖，适合几个人内部使用
- 缓存目录定时清理，避免 `temp/` 和 `cache/` 持续膨胀

额外建议：

- 不要把它当大规模公网下载站来跑
- 尽量让前端默认走 `direct`
- 如果代理压力大，可以优先尝试 `redirect_url`
- 如果你的服务器磁盘较小，减少使用 `download` 模式
- 当前自动清理只处理 `temp/` 和 `cache/`，不会自动清理 `output/`

## 9. 缓存与文件清理策略

当前内置清理策略如下：

- 清理间隔：`6` 小时
- 保留时长：`6` 小时
- 清理目录：
  - `temp/`
  - `cache/`

不会被这套清理器自动删除的内容：

- `output/` 里的真实下载成品
- `.file-index.json` 中登记的下载结果

这意味着：

- 直链模式产生的临时缓存会被自动回收
- 下载模式产生的最终文件需要你自己决定是否长期保留
- 如果后续下载模式用得多，建议补一个独立的 `output/` 生命周期清理策略

## 10. 接口概览

### 10.1 基础接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/` | 服务入口 |
| `GET` | `/docs` | Swagger 文档 |
| `GET` | `/api/v1/health` | 健康检查 |
| `GET` | `/api/v1/history` | 最近任务历史 |

### 10.2 解析与任务接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/v1/parse` | 创建解析任务 |
| `GET` | `/api/v1/tasks/{task_id}` | 查询任务状态 |
| `GET` | `/api/v1/tasks/{task_id}/result` | 获取任务结果 |
| `GET` | `/api/v1/tasks/{task_id}/redirect?kind=single\|video\|audio` | 获取项目重定向地址 |
| `GET` / `HEAD` | `/api/v1/tasks/{task_id}/proxy?kind=single\|video\|audio` | 获取项目代理地址 |

### 10.3 下载文件接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/v1/files/{file_id}/download` | 下载后端托管的最终文件 |

## 11. 请求示例

### 11.1 默认直链模式

```http
POST /api/v1/parse
Content-Type: application/json

{
  "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "delivery_mode": "direct"
}
```

### 11.2 强制下载模式

```http
POST /api/v1/parse
Content-Type: application/json

{
  "url": "https://www.bilibili.com/video/BV1Mj411P7mA/",
  "delivery_mode": "download"
}
```

### 11.3 健康检查返回示意

```json
{
  "status": "ok",
  "app_name": "VideoParse API",
  "cleanup_interval_hours": 6,
  "cleanup_retention_hours": 6,
  "api_public_origin": "http://127.0.0.1:8000",
  "yt_dlp_available": true,
  "ffmpeg_available": true,
  "default_delivery_mode": "direct",
  "supported_platforms": [
    "bilibili",
    "douyin",
    "twitter",
    "youtube",
    "reddit"
  ]
}
```

## 12. 平台层面的现实说明

当前项目底层依赖 `yt-dlp`，不同平台的行为以实际返回结果为准。

### `Bilibili`

- 很多视频没有单文件直链
- 常见情况是返回分离流
- 当前项目已支持返回 `video_proxy_url` / `audio_proxy_url`
- 如果目标必须是一个单文件 URL，建议走 `download`

### `YouTube`

- 既可能拿到单文件可播流，也可能出现分离流
- 当前实现已验证单文件代理链路可用
- 下载模式已限制在更保守的格式范围，避免小服务器压力过大

### `Twitter / X`

- 有些帖子可以直接得到单文件媒体
- 有些帖子没有公开视频资源，或受登录态、地区、帖子状态影响
- “可以下载但没有单文件直链”在这个平台上并不罕见

### `Douyin` / `Reddit`

- 当前也走 `yt-dlp` 统一提取
- 是否返回单文件流取决于源站实际资源结构

## 13. Ubuntu 部署建议

### 13.1 安装系统依赖

```bash
sudo apt update
sudo apt install -y ffmpeg python3 python3-venv python3-pip nodejs npm
```

### 13.2 启动后端

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

### 13.3 启动前端

```bash
cd frontend
npm install
npm run dev
```

开发环境默认地址：

- 前端：`http://127.0.0.1:5173`
- 后端：`http://127.0.0.1:8000`
- 健康检查：`http://127.0.0.1:8000/api/v1/health`
- 接口文档：`http://127.0.0.1:8000/docs`

前端开发服务器已经配置了 `/api` 代理到 `http://127.0.0.1:8000`。

## 14. 关键环境变量

后端配置位于 `backend/app/core/config.py`，建议至少关注下面这些：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `FRONTEND_ORIGIN` | `http://127.0.0.1:5173` | 前端来源，用于 CORS |
| `API_PUBLIC_ORIGIN` | `http://127.0.0.1:8000` | 生成对外可访问 URL 的基准地址 |
| `CLEANUP_INTERVAL_HOURS` | `6` | 缓存清理周期 |
| `CLEANUP_RETENTION_HOURS` | `6` | 缓存保留时间 |
| `YT_DLP_DOWNLOAD_FORMAT` | `best[height<=1080]/bestvideo*[height<=1080]+bestaudio/best` | 下载模式的默认格式策略 |
| `YT_DLP_MERGE_OUTPUT_FORMAT` | `mp4` | 合流输出格式 |
| `FFMPEG_LOCATION` | 空 | `ffmpeg` 可执行文件路径，可选 |
| `PROXY_TIMEOUT_SECONDS` | `30` | 代理请求超时 |
| `PROXY_CHUNK_SIZE` | `65536` | 代理分块大小 |
| `PROXY_MAX_CONNECTIONS` | `20` | 代理连接上限 |

前端可选环境变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `VITE_API_BASE_URL` | `/api/v1` | 前端请求 API 的基础路径 |

生产环境最关键的一项是：

```env
API_PUBLIC_ORIGIN=https://your-domain-or-public-ip
```

如果这里还是本地地址，那么返回给用户的 `proxy_url`、`redirect_url`、`download_url` 都会指向错误位置。

## 15. 当前限制

当前版本更偏向“小团队内部可用骨架”，还存在这些限制：

- 任务和历史记录当前保存在内存中，服务重启后会丢失
- 还没有接入数据库和 Redis
- 还没有真正的异步任务队列
- `output/` 没有独立生命周期管理
- 部分平台可能需要 cookies、登录态、地区能力
- 不处理 DRM、付费绕过或受限内容破解

## 16. 后续迭代建议

建议后续按下面顺序推进：

1. 把任务存储迁移到数据库
2. 引入 Redis / 队列系统处理异步任务
3. 为下载成品增加生命周期清理策略
4. 增加 cookies / 登录态支持
5. 根据不同平台做更细的直链筛选策略
6. 为生产环境补充 Nginx、进程守护与日志采集

## 17. 当前结论

这个项目现在已经不是最初的“规划文档阶段”，而是一个可运行的基础版本：

- 已接入真实下载器 `yt-dlp`
- 已支持项目生成的代理直链和重定向直链
- 已针对 `1C1G Ubuntu` 服务器做了默认策略收敛
- 已把缓存清理周期固定为每 `6` 小时
- 已把主要使用目标调整为“拿到可用地址并转发”，而不是默认重下载所有视频

如果后面继续扩展，最重要的不是再堆更多平台，而是先把“任务持久化、下载文件生命周期、生产环境部署链路”补完整。
