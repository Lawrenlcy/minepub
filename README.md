# Minepub

本机运行的 PDF → EPUB / AZW3 Web 应用。中文界面，支持文字版和扫描版 PDF，使用 **MinerU 4.x Python SDK** 直接将 `MiddleJson` 渲染成 EPUB，不经过 Markdown；再由本机 Calibre 转为 Kindle AZW3。

## 在这台 Windows 电脑上使用

双击 `start.bat`，浏览器打开 <http://127.0.0.1:8765>。保留启动窗口，按 `Ctrl+C` 停止服务。已有服务时会直接打开页面。

也可以在 PowerShell 中运行：

```powershell
cd D:\minepub
.\start.ps1
```

当前项目使用 `.venv` 隔离环境。首次在其他电脑启动时，脚本会创建 Python 3.11 环境并安装依赖；先安装 Python 3.11 或 uv。若 PowerShell 不允许运行脚本，使用 `start.bat` 即可，无需永久更改系统执行策略。

手动安装、启动：

```powershell
uv venv .venv --python 3.11
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
$env:MINERU_MODEL_SOURCE = 'modelscope'
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

## 如何选择解析方式

| PDF 类型 | 解析质量 | 文字识别 |
| --- | --- | --- |
| 有正常文字层的简单文档 | 快速 | 读取文字层 |
| 扫描书、拍摄文档 | 基础或标准 | 自动 / 强制 OCR |
| 图文混排、多栏、复杂表格 | 标准 | 自动 |
| 需要投入更多计算提升识别 | 精细 | 自动 |

默认「标准 + 自动」，由 MinerU 判断读取文字层还是 OCR。快速文字层模式不需要推理模型，但不能识别纯扫描页；它也不提供与标准模式相同的复杂版面理解能力。

首次模型解析需要下载模型，本项目默认从 ModelScope 下载。后续复用 MinerU 的本地缓存。基础模式使用小模型，标准/精细模式还会加载视觉语言模型。当前基础安装的 ONNX 默认在 CPU 上运行，存在 NVIDIA 显卡不代表所有步骤都会使用 GPU。模型下载及推理情况见每个任务的「日志」。

EPUB 是可重排格式，结构、图片、目录的识别质量取决于原始 PDF 和所选解析模式；不会逐页复刻 PDF 排版。

## Kindle / AZW3

输出格式默认「EPUB + AZW3」，完成后分别下载。AZW3 用 USB 复制到 Kindle 的 `documents` 文件夹，安全弹出后等待设备索引；设备需支持 AZW3 / KF8。EPUB 不能通过 USB 直接这样阅读。此流程不依赖 Send to Kindle 或亚马逊账号。

采用 **PDF → MinerU 解析/OCR → EPUB → Calibre AZW3**，复用 MinerU 已识别的正文、图片和目录，避免让另一个转换器重新猜测 PDF 阅读顺序。AZW3 转换保留输入字号和目录，关闭自动章节检测；扫描识别错误仍需在 PDF 解析阶段解决。

已有 EPUB 的任务可点击「生成 AZW3」，无需再次解析 PDF。如果 AZW3 步骤失败或取消，已生成的 EPUB 仍可下载，也能重试 AZW3。

这台电脑已安装项目内的 Calibre Portable。迁移到其他 Windows 电脑时，先运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup-azw3.ps1
```

脚本从 Calibre 官网下载便携版并验证数字签名，安装到 `tools/calibre/`。也可使用已安装的 Calibre，或通过 `MINEPUB_EBOOK_CONVERT` 指定 `ebook-convert.exe` 的完整路径。未找到工具时仍可选择「仅 EPUB」。AZW3 步骤最多运行 10 分钟。

## 应用行为

- 单文件最大 200 MB、2000 页，最多排队 10 个任务。
- 串行后台转换，每个任务有独立子进程，避免并行模型加载耗尽内存。
- 显示真实阶段：排队 → 加载 → 解析 → 生成 EPUB → 生成 AZW3（如选择）→ 完成；解析阶段没有伪造的百分比。
- 可取消、分别下载 EPUB / AZW3、给已有任务补生成 AZW3、查看日志、删除文件和任务记录。
- SQLite 保存任务状态；刷新页面可恢复当前浏览器最近 50 个任务。服务重启后恢复排队任务，将中断任务标记失败。
- 完成、失败、取消后的文件和记录保留 24 小时，服务运行时自动清理。关闭服务期间不会执行清理，下次启动继续。
- 默认单个任务 2 小时超时，可按需要调大。
- PDF 不会上传到官方在线转换服务。程序调用本机 `mineru.parser.parse()`；模型下载会联网。自定义 MinerU 配置时，应避免设置远程推理地址，才能保持所有解析在本机。
- 只监听 `127.0.0.1`，并校验 Host / Origin / Fetch Metadata；这是单用户本地应用，没有公网账号体系。不要用多个 Uvicorn worker 启动同一个数据目录。

配置在启动前设置的环境变量中：

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `MINEPUB_MAX_UPLOAD_MB` | `200` | 上传文件大小上限 |
| `MINEPUB_TIMEOUT_SECONDS` | `7200` | 单任务运行时限，包含模型下载 |
| `MINEPUB_DATA_DIR` | 项目 `data/` | 上传文件、结果、任务数据库及日志 |
| `MINERU_MODEL_SOURCE` | `modelscope` | MinerU 模型下载源 |
| `MINEPUB_EBOOK_CONVERT` | 自动查找 | 可选，指定 Calibre 转换程序的完整路径 |

## 开发与验证

```powershell
uv pip install --python .venv\Scripts\python.exe -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -q
node --check static/app.js
```

后端测试覆盖：上传、状态、取消、删除、非法/损坏/加密 PDF、流式上传大小限制、队列上限、下载、过期清理及本地访问边界。实际模型转换需另外使用真实 PDF 验证，单元测试不会假装调用真实 OCR。

已在此电脑使用 MinerU 4.0.12 验证：

- 147 页文字版书籍：快速 + 文字层模式，生成 EPUB，检查结果包含第 0–146 页的内容、目录、图片和书末正文；缓存热身后约 18 秒。复杂多栏目录的阅读顺序仍需选标准模式并人工复核。
- 从示例书渲染得到的 1 页纯图片 PDF：输入文字层为零，快速 + 强制 OCR 模式成功识别中文并生成 EPUB。
- 1 页中文正文：标准 + 自动模式成功生成 EPUB，首次运行下载了所需视觉模型。
- 1 页纯图片 PDF：标准 + 自动模式自动识别扫描内容并生成 EPUB，模型缓存后约 38 秒，中文正文与文件名书名元数据均验证通过。
- 浏览器上传、完成状态、刷新恢复、下载和日志查看；390px 与 1280px 两种宽度布局。
- 21 项自动测试通过，包括 AZW3 工具缺失、重试、取消保留 EPUB、下载格式、队列上限、失败清理和拒绝伪装格式。EPUB 校验包含 ZIP、XML、资源引用和目录锚点，不等同于 EPUBCheck 全规范认证或逐字校对。
- 使用 Calibre 9.16.0 将示例书转为 AZW3，检查 Palm / MOBI / KF8 头，重新解码后原 EPUB 的 194269 个非空白正文字符完整保留，59 个目录条目一致。额外生成了封面和目录页；未在实体 Kindle 上测试。
- 新上传的 1 页文字 PDF 一次生成 EPUB + AZW3；1 页纯扫描 PDF 使用标准 + 自动模式完成 OCR → EPUB → AZW3，缓存后约 13 秒。浏览器中已验证历史 147 页示例书补转和 AZW3 下载。

可单独校验 EPUB 容器与引用：

```powershell
.venv\Scripts\python.exe scripts/check_epub.py path\to\book.epub
```

主要文件：

- `app/main.py`：FastAPI、文件校验、SQLite、串行队列和下载接口。
- `app/worker.py`：独立 MinerU 解析进程，原生 EPUB 渲染与容器校验。
- `app/calibre.py`：本机 EPUB → AZW3 转换、KF8 文件头检查与原子发布。
- `setup-azw3.ps1`：安装并验证官方 Calibre Portable。
- `static/`：无构建步骤、无 CDN 依赖的前端。
- `start.bat` / `start.ps1`：Windows 启动入口。
- `data/`：运行时数据，已加入 `.gitignore`。

接口：`POST /api/jobs?filename=book.pdf&tier=standard&mode=auto&output_format=both` 接收 `Content-Type: application/pdf` 的原始文件字节；`output_format` 可为 `epub`（API 默认）或 `both`；`GET /api/jobs/{id}` 查询状态和可下载格式；`POST /api/jobs/{id}/azw3` 从已有 EPUB 补转；`GET /api/jobs/{id}/download?format=epub` 或 `format=azw3` 下载。

## 官方接口依据

- [MinerU Python SDK](https://opendatalab.github.io/MinerU/usage/sdk_api/)
- [原生渲染格式](https://opendatalab.github.io/MinerU/reference/output_files/)
- [解析档位与运行环境](https://opendatalab.github.io/MinerU/usage/tiers/)
- [Calibre 官方便携版](https://calibre-ebook.com/download_portable)
- [Calibre ebook-convert](https://manual.calibre-ebook.com/generated/en/ebook-convert.html)
