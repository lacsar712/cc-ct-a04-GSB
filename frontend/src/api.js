const TOKEN_KEY = "cnc_offset_token";
const USER_KEY = "cnc_offset_user";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function getUser() {
  const raw = localStorage.getItem(USER_KEY);
  return raw ? JSON.parse(raw) : null;
}

export function setSession(token, user) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

async function request(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(`/api${path}`, { ...options, headers });
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text };
  }
  if (!res.ok) {
    const msg = data?.detail || data?.message || `请求失败 (${res.status})`;
    const err = new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    err.status = res.status;
    throw err;
  }
  return data;
}

export function login(username, password) {
  return request("/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export function fetchSubmissions() {
  return request("/submissions");
}

export function fetchSubmission(id) {
  return request(`/submissions/${id}`);
}

export function createSubmission(tool_code, offset_um, is_urgent) {
  return request("/submissions", {
    method: "POST",
    body: JSON.stringify({
      tool_code,
      offset_um: Number(offset_um),
      is_urgent: Boolean(is_urgent),
    }),
  });
}

// 双车道台数据：急补/普通两车道与下一笔将领全部由后端同一排序函数给出。
export function fetchLanes() {
  return request("/desk/lanes");
}

// 认领下一笔。target_id 传后端给出的 next.id，后端不匹配会拒绝。
export function claimNext(targetId) {
  return request("/submissions/claim", {
    method: "POST",
    body: JSON.stringify(targetId == null ? {} : { target_id: targetId }),
  });
}
