# 界面走查（端到端）

真起一次本机服务（后端 + 构建好的界面），只把识别换成假的：不下模型、不调模型、不花钱，一分钟内走完。

走两条路：
- 打开 → 选文件 → 开始转录 → 看到带说话人的稿子；
- 同样走一遍，但转录失败：页面要说清楚失败了、让人重试，而且不提计费。

## 跑

```bash
cd frontend
npm ci && npx playwright install chromium   # 只在第一次
npm run e2e                                 # 先构建界面，再起服务走查
```

- 用的 Python 要装好后端依赖（fastapi 等）。不叫 `python3` 就设 `E2E_PYTHON`。
- 端口默认 8790，被占了设 `E2E_PORT`。
- 数据放在系统临时目录，不碰 `~/.transcribe-local`。
- 上传时后端用 ffprobe 量时长，要装 ffmpeg。

## 哪里是假的

- **识别**：`backend/tests/e2e_orchestrator.py`，一秒后给出固定两句；录音长于 3 秒时故意失败。
- **模型齐不齐**：拦下 `/api/local/models`，说「齐了」。首次下载模型那一页不在这里走。

其余都是真的：上传、量时长、排队、后台处理、存结果、取稿。
