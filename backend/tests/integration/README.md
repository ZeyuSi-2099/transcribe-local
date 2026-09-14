# 端到端集成测试（手动）

1. 填好 `server/.env`（四路 ASR 的全部 key + 阿里 OSS 凭证）。
2. 准备一段 **30–60 秒** 的中文访谈音频，放到 `server/_local/sample_zh.m4a`。
3. 确认本机已 `claude` 登录（订阅会话在 `~/.claude`）。
4. 运行：
   ```bash
   docker compose run --rm \
     -e E2E_AUDIO=/data/sample_zh.m4a \
     transcribe pytest -m integration tests/integration -v
   ```
5. 期望：测试通过，且 `docker compose run` 里能看到各 phase 日志。
   随后可跑真出稿：
   ```bash
   docker compose run --rm transcribe \
     python -m transcribe_cli /data/sample_zh.m4a --out /data/result.json
   ```
   检查 `server/_local/result.json` 是带 {t,s,sp} 的真实转录。
