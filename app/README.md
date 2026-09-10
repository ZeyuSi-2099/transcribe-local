# 桌面外壳（M3）

Tauri 2 + React。前端是纯静态、后端是 HTTP，所以这个壳只是把 `127.0.0.1` 装进一个窗口 ——
「浏览器打开」和「桌面应用」在界面代码上是同一份。

尚未开始。M3 之前这里是空的，用命令行入口开发：

```bash
python3 -m transcribe_local run <音频>
```

## 待办

- [ ] Tauri 骨架 + Python sidecar（PyInstaller，注意 sherpa-onnx 的 .dylib 要手工补）
- [ ] 复用 Transcribe 的设计 token（`tokens.css`），补一套暗色
- [ ] 六屏：新建 / 运行中 / 结果 / 设置 / 术语库 / 历史
- [ ] macOS 签名与公证（需要 Apple Developer 账号）
