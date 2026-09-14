// 会话 token 存取（P1 真实登录）：localStorage 持久化，30 天有效期由后端控制。
const KEY = "tx_token";

export function getToken(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string): void {
  try {
    localStorage.setItem(KEY, token);
  } catch { /* 隐身模式等存不了就算了：本次会话内仍可用 */ }
}

export function clearToken(): void {
  try {
    localStorage.removeItem(KEY);
  } catch { /* noop */ }
}

export function authHeaders(): Record<string, string> {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}
