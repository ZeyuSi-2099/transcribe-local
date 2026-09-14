# 里程碑 B 端到端验证

## A. 免费桩验证（验证 API+队列+存储 整条链路，不花钱）
1. `docker compose up -d`（postgres+minio+api+worker 全起）。
2. 在能连 localhost:8000 + Postgres + MinIO 的环境（设好 DATABASE_URL / S3_ENDPOINT）跑：
   `pytest -m infra tests/integration/test_api_e2e.py -v`
   期望：上传→排队→worker(桩)→存结果→取结果 全链路绿。

## B. 真跑验证（真四路 ASR，花钱）
1. 填好 `server/.env`（四路 ASR key + OSS 凭证），确认 `~/.claude` 已登录。
2. `docker compose up -d`，worker 服务即用真实 `transcribe`。
3. 上传：`curl -F file=@sample_zh.m4a -F lang=zh http://localhost:8000/api/jobs`
4. 轮询：`curl http://localhost:8000/api/jobs/<jobId>`，phase 从 P0 走到 done。
5. 取结果：`curl http://localhost:8000/api/jobs/<jobId>/result` 得带说话人的 {t,s,sp}。
