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

## 架构

```
音频 → P0 转码
     → 声纹分段（pyannote-segmentation-3.0 + CAM++）+ VAD 补漏 → 18 秒切块
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
| 本机服务（标准库） | `src/transcribe_local/server.py` |
| 界面 | `web/index.html`（单文件，无构建步骤） |
| 模型清单与下载 | `src/transcribe_local/models.py` · `models/manifest.toml` |
| 融合提示词 | `prompts/merge.zh.md` |
| 全部参数 | `config.default.yaml` |

## 跑

```bash
PYTHONPATH=src python3 -m transcribe_local setup              # 首启向导：查依赖 → 下模型 → 写 config.yaml
PYTHONPATH=src python3 -m transcribe_local setup --minimal    # 只装一路（271 M），先出一份稿子
PYTHONPATH=src python3 -m transcribe_local doctor            # 查依赖与模型
PYTHONPATH=src python3 -m transcribe_local models pull       # 下模型（下载 2.3 G / 占盘 2.7 G）
PYTHONPATH=src python3 -m transcribe_local serve             # 起本机服务，界面在浏览器里开
PYTHONPATH=src python3 -m transcribe_local run 音频.m4a       # 跑全链（命令行）
PYTHONPATH=src python3 -m transcribe_local run 音频.m4a --no-fuse   # 只跑到分歧册
PYTHONPATH=src python3 -m transcribe_local config --explain chop.max_length
```

开发时想复用已有的模型缓存：`TRANSCRIBE_LOCAL_MODELS=~/.cache/sherpa-onnx-models`。

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

`prompts/merge.zh.md` **是生成的，不要手改。** 它从我们生产线那份融合提示词同步而来 ——
判断规则逐字相同（72% 字符原封不动），只有四处不同，且都是「本地根本没有那个东西」：
没有主轨 · 说话人来自共用声纹分段 · 不联网 · 不写文件不出报告。

这样做的理由：两份各自手改一定会漂，而 P3 的 run-to-run 方差本来就大 ——
漂了之后「本地版成绩不如生产线」到底是提示词差异还是引擎差异，根本分不清。

**大模型对措辞极其敏感，改动要最小化**；单样本会骗人，**同配置至少跑 3 遍比均值**再下结论。

## 待办

- [ ] **同配置各跑 3 遍取均值，换掉 README 里那张表的单样本数字。**
      现在那些数是各跑一次的结果（已在 README 里注明不是平均值）。这一步方差很大，单样本会骗人。
- [x] 模型许可与 `sha256` 已补齐（默认那七个；来源见 `license_src`）
- [ ] `seaco_paraformer` 那条的 URL 指的是 `paraformer-zh-small`，不是 SeACo —— 用之前先核对
- [x] BSL 的 Licensor 与 Change Date 已填实（Change Date = 2030-10-01）
- [x] 本机 HTTP 服务 + 浏览器界面（`web/`）—— 六屏已通，标准库起服务、无新依赖
- [ ] 英文 profile

### 远期

- [ ] **桌面外壳（Tauri 把本机的 `127.0.0.1` 装进一个窗口）** —— 可选项，**不挡发布**。
      界面代码与浏览器界面是同一份，壳只是换个容器。
- [ ] **注册 Apple Developer 账号**（公司主体要 D-U-N-S 编号，通常要排一到两周）。
      **只在要做桌面外壳时才需要**：没有它 macOS 版签不了名，用户下载后会被 Gatekeeper 拦住，
      而 macOS 15 之后右键「打开」那招已经取消，得去系统设置点「仍要打开」或手敲 `xattr`。
      ⚠️ Windows 同理要买代码签名证书（OV/EV，按年付）才能免掉 SmartScreen ——
      **签名不是苹果一家的成本**，它是「双击图标」这条路的门票，而浏览器界面这条路不用买票。
