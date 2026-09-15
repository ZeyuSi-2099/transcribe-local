# CLAUDE.md

## 这是什么

**Transcribe Local** —— [Transcribe](https://transcribe.solutions) 的本地开源版。
四台解码路线互不相同的本地 ASR 转同一段录音，用代码穷举定位分歧，再交给一个大模型定字。
全程本机、不联网、不上传。

**这是一个要公开的仓库。** 写任何东西之前先想一遍：这行内容公开了会怎样。

## 三条红线

1. **客户音频、金标、逐条人工判定，永远不进这个仓库。** `.gitignore` 按扩展名挡了音频，
   但文本形式的转录内容挡不住 —— 靠人。示例音频只能用公共域素材。
2. **密钥只从环境变量读。** 不写进配置文件、不进日志、不进报错信息。
3. **内容不能丢。** 任何一步都不许静默删掉识别出来的内容 —— 删掉的话用户在稿子上看不见、
   也进不了复核队列，等于悄悄丢了话还不留痕。定不下来的字标 `[❓]`，不要抹掉。

## 与上游的关系

研究在内部仓 `ASR-Optimizer`（私有）里做 —— 横评、金标、逐条判定、否决记录都在那边。
这个仓库只拿走**已经定下来的结果**。单向同步，开源仓不回写研究数据。

社区提的参数改进，先在内部仓复测，再进这里的默认值。

**与线上 SaaS 的关系**（Duner 2026-09-14 定）：本地版以线上 SaaS 为底本做减法 —— 线上代码整块拿，
只换底座（数据库、文件存放、登录、后台调度、识别）。线上每个文件拿不拿、改不改，登记在 `sync/saas.yaml`。
线上更新后先跑 `python3 tools/saas_sync_check.py`；本地要改一个登记为「原样同步」的文件，先登记成 `modify` 再改。流程见 `docs/saas-sync.md`。

**拿进来之前必须过 `tools/saas_scan.py`**：

```bash
TRANSCRIBE_SCAN_WORDS=<内部仓私有词表> python3 tools/saas_scan.py   # 待看 0、未登记 0 才算过
```

- 私有词表（客户专名、录音编号）**只在内部仓**，这里只从环境变量读路径；不给词表脚本拒绝运行。
- 人工确认可以公开的命中记进 `sync/saas_scan_allow.txt` —— **只记行指纹和理由，不记原文**。
- 清点时扫出的真实内容（访谈原话、客户项目术语与大纲、注释里的录音编号；无联系方式、密钥、受访者真名），
  Duner 2026-09-15 看过逐项清单后定**原样拿进来**，命中逐行登记在放行清单里。线上以后新加的内容照样逐行看。

## 架构

```
音频 → P0 转码
     → 声纹分段（pyannote-segmentation-3.0 + CAM++）+ VAD 补漏 → 30 秒切块
     → P1 四路 ASR 并行（吃同一份切块）
     → 分歧册（纯 Python 穷举定位写法不一致的片段）
     → P3 大模型定字（OpenAI 协议）
     → 导出
```

**没有 P2。** 四路吃同一份切块 + 同一份声纹分段，第 N 块在四路里是同一段音频，对齐是恒等的。
云端生产线的 P2 模糊对齐在这里无事可做 —— 顺带也没有它「裁掉读音不近的证据」那个已知损耗。

| 模块 | 文件 |
|---|---|
| 转码与读音频 | `src/transcribe_local/audio.py` |
| 声纹 + VAD + 切块 | `src/transcribe_local/diarize.py` |
| 整条链跑一遍 | `src/transcribe_local/pipeline.py`（命令行和界面共用这一个） |
| 四路 ASR | `src/transcribe_local/engines.py` |
| 分歧册 | `src/transcribe_local/divergence.py` |
| 融合（OpenAI 协议） | `src/transcribe_local/fuse.py` |
| 导出 | `src/transcribe_local/export.py` |
| 本机服务（以线上后端为底本） | `backend/`（FastAPI + SQLite；转录入口 `backend/pipeline/local_orchestrator.py`） |
| 界面（以线上界面为底本） | `frontend/`（React + Vite；`serve` 挂的是构建好的 `frontend/dist`） |
| 线上 → 本地同步 | `sync/saas.yaml` 登记 · `tools/saas_sync_check.py` 巡检 · 流程 `docs/saas-sync.md` |
| 模型清单与下载 | `src/transcribe_local/models.py` · `models/manifest.toml` |
| 融合提示词 | `src/transcribe_local/_merge_zh.py`（生成的，在包里，不是单独文件） |
| 全部参数 | `config.default.yaml` |

## 跑

```bash
PYTHONPATH=src python3 -m transcribe_local setup              # 首启向导：查依赖 → 下模型 → 写 config.yaml
PYTHONPATH=src python3 -m transcribe_local setup --minimal    # 只装一路（271 M），先出一份稿子
PYTHONPATH=src python3 -m transcribe_local doctor            # 查依赖与模型
PYTHONPATH=src python3 -m transcribe_local models pull       # 下模型（下载 2.3 G / 占盘 2.7 G）
(cd frontend && npm ci && npm run build)                     # 界面先构建一次（改了 frontend/ 要重新构建）
PYTHONPATH=src python3 -m transcribe_local serve             # 起本机服务（127.0.0.1:8765），界面在浏览器里开
PYTHONPATH=src python3 -m transcribe_local run 音频.m4a       # 跑全链（命令行）
PYTHONPATH=src python3 -m transcribe_local run 音频.m4a --no-fuse   # 只跑到分歧册
PYTHONPATH=src python3 -m transcribe_local config --explain chop.max_length
```

开发时想复用已有的模型缓存：`TRANSCRIBE_LOCAL_MODELS=~/.cache/sherpa-onnx-models`。

改界面时不用每次构建：`serve --no-open` 起接口，另开 `cd frontend && npm run dev`（vite 把 /api 代理到 8765）。

测试：

```bash
PYTHONPATH=src python3 -m pytest -q tests                                   # 根目录（识别层 + 同步工具）
cd backend && python3 -m pytest -q                                           # 后端（线上测试里本机没有的功能登记在 conftest.py 的 NOT_APPLICABLE，自动跳过）
cd frontend && npx tsc --noEmit && npx vitest run && npm run build          # 界面
```

## 改参数之前

`config.default.yaml` 里每个参数都带来源标注：

- `[定档]` 有全长实测支撑 —— **改之前先读注释里的理由**，很多是踩过坑换来的
- `[未验证]` 没有实测证据，可以放心试
- `[有更好的]` 已经量出更好的做法但没落地
- `[缺陷]` 已知有问题

几条最容易被「优化」掉的：

- **归堆人数写死 2。** 多给名额不会去找第三人，会去劈主说话人。
- **VAD 不是可选项。** 声纹分段单用会漏 215 个真字，取并集后只漏 4 个。
- **短片段筛选不可信，结论一律跑全长。** 有方案在 10 分钟片段上声纹准确率 99.8%，
  同一条录音跑全长塌到 63.9%。
- **AED 引擎跑两次不是同一份稿子。** 下结论前同配置至少跑两遍。
- **附和词表（23 字）和语气词表（19 字）故意不同，别合并。** 前者判「整块是不是纯应答」，
  后者判「块内这处差异有没有意思」。合表实测会把 16 处真差异折掉。

## 改提示词之前

`src/transcribe_local/_merge_zh.py` **是生成的，不要手改。** 它从我们生产线那份融合提示词同步而来 ——
判断规则逐字相同（72% 字符原封不动），只有四处不同，且都是「本地根本没有那个东西」：
没有主轨 · 说话人来自共用声纹分段 · 不联网 · 不写文件不出报告。

这样做的理由：两份各自手改一定会漂，而 P3 的 run-to-run 方差本来就大 ——
漂了之后「本地版成绩不如生产线」到底是提示词差异还是引擎差异，根本分不清。

**放进代码而不是放成一个 `.md`**，是为了挡住「随手打开改一行」。
⚠️ **这不是加密，也不要对外说成加密**：任何人把 `base_url` 指向自己的一个小服务，
就能原样收到这段文字（实测 20 行 Python 即可）。真正的商业保护在 `LICENSE`（BSL 1.1），不在这里。

**大模型对措辞极其敏感，改动要最小化**；单样本会骗人，**同配置至少跑 3 遍比均值**再下结论。

## 待办

- [x] **同配置各跑 3 遍取均值，换掉 README 里那张表的单样本数字。**（2026-09-12 完成，`tools/bench.py` 一条命令出均值与极差）
- [x] 模型许可与 `sha256` 已补齐（默认那七个；来源见 `license_src`）
- [x] 热词那条路：**不做**（Duner 2026-09-11 定，热词伤引擎权重）。SeACo 适配器分支与配置项已摘。
- [x] BSL 的 Licensor 与 Change Date 已填实（Change Date = 2030-10-01）
- [x] 本机 HTTP 服务 + 浏览器界面 —— 2026-09-15 起换成以线上为底本的 `backend/` + `frontend/`，旧的单文件界面与标准库服务已删
- [x] 发布包自带构建好的界面（Duner 2026-09-15 定：构建产物不进 git，发版时由 CI 构建，`hatch_build.py` 放进包）。
      发版：`git tag v<pyproject 里的 version> && git push origin v<同上>`，流程见 `.github/workflows/release.yml`
- [ ] 传 PyPI：Duner 在 PyPI 给本仓库配「可信发布」（环境名 `pypi`），再在仓库变量里加 `PUBLISH_PYPI=true`
- [ ] 英文 profile

### 远期

> 2026-09-14 Duner 定：准确度的进一步优化统一放到远期，等「本地准确度优化与固化」专项时再做，**现在不测**。

**准确度（下列数字均为 30 秒切块 + 按换人拆块 + 线上同款定字的全长实测，内部仓 `实测记录/speaker_split_2026-09-14/`）**

- [ ] **定字环节损失约 20 处（占终稿错的四成）。** 每遍约 12 处是别的引擎写对了定字没采纳，约 8 处是第一台本来写对被定字改错。
      多为润色删句首句尾字、两阶段专名表把两个不同的说法统一成了一个。
      可选方向：本地默认走 Claude 订阅路（18 秒时 46.3 vs DeepSeek 两阶段 60.3，30 秒拆块未测）；把「删字」「专名表统一」反馈给线上。
- [ ] **专名近音（四台一起听错里约一半）。** 可选方向：Fun-ASR-Nano 作第五台（18 秒时四路全错 50 → 47，30 秒未测）；术语库已有效。
- [ ] **两人同时开口时声音小的一方被漏识。** 四路全错里约 8 处（如「当然」「链接」「不客气」）。与说话人碎片同根，需要声纹能标出重叠。
- [ ] **切块 / 静音检测：已核对不是抓手。** 漏字不集中在切块边界，未切进块的空隙全长仅 17 秒。记在这里免得重复查。
- [ ] **说话人碎片（按换人拆块之后）。** 换人点边上一两个字被判给另一个人，30 秒全长逐字说话人 98.3%，错字约 140 个。
      成因：约 2/3 是声纹段边界与识别逐字时间每次误差不同（不是固定偏移，平移无效），约 1/5 是两人同时开口、声纹没标重叠。
      已试且否决：固定规则（短段认领）、平移对齐、换时间戳引擎、声纹参数、声纹复核切点、后置 Sonnet 按文字判定（Duner 2026-09-14：用文字校准转录层的错不稳固）。
      可能的方向：声纹引擎给出逐帧的说话人概率或重叠标记。实验记录在内部仓 `实测记录/speaker_split_2026-09-14/`。

**说话人识别**

- [ ] **多人录音（三人及以上）。** 目标场景是座谈会、多人访谈，把说话人识别做准。现在 `diarize.num_clusters` 写死 2。
      单独立项；内部仓已有一条 2 小时 49 分的三人素材，但没有标准答案。

**与线上 SaaS 同步**

- [ ] **线上遗留问题：删除全部转录 / 注销被拒时报 500。** 线上 `server/app/api.py` 两处 `UserError(422, e.code)`，
      而 `DeleteRejected` 没有 `code` 属性 —— 有任务在跑时点删除，用户看不到「还有任务在进行中」那句，只看到出错。
      本地 `backend/app/api.py` 的删除转录已改为 422 带原话并补了测试；线上暂不改（Duner 2026-09-14 定为远期）。
      线上修了之后，本地这一处跟着同步回线上写法。

**部署**

- [ ] **fly.io 云端省内存版本。** 优先 ONNX 类引擎、控制常驻内存。排在所有事项的最后。
- [ ] **桌面外壳（Tauri 把本机的 `127.0.0.1` 装进一个窗口）** —— 可选项，**不挡发布**。
      界面代码与浏览器界面是同一份，壳只是换个容器。
- [ ] **注册 Apple Developer 账号**（公司主体要 D-U-N-S 编号，通常要排一到两周）。
      **只在要做桌面外壳时才需要**：没有它 macOS 版签不了名，用户下载后会被 Gatekeeper 拦住，
      而 macOS 15 之后右键「打开」那招已经取消，得去系统设置点「仍要打开」或手敲 `xattr`。
      ⚠️ Windows 同理要买代码签名证书（OV/EV，按年付）才能免掉 SmartScreen ——
      **签名不是苹果一家的成本**，它是「双击图标」这条路的门票，而浏览器界面这条路不用买票。
