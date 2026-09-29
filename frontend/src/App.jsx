import { createSignal, onMount, onCleanup, Show, For, createEffect } from "solid-js";
import {
  claimNext,
  clearSession,
  createSubmission,
  fetchLanes,
  fetchSubmission,
  fetchSubmissions,
  getUser,
  login,
  setSession,
} from "./api";

const statusLabel = {
  pending: "待复核",
  processing: "复核中",
  done: "已完成",
};

const roleLabel = {
  machinist: "操作员",
  auditor: "复核员",
};

function readHash() {
  const raw = (location.hash || "#/").replace(/^#/, "") || "/";
  const m = raw.match(/^\/detail\/(\d+)/);
  if (m) return { name: "detail", id: Number(m[1]) };
  if (raw === "/desk") return { name: "desk", id: null };
  return { name: "home", id: null };
}

function UrgentTag({ urgent }) {
  return urgent ? <span class="tag urgent">急补</span> : <span class="tag normal-tag">普通</span>;
}

function App() {
  const [user, setUser] = createSignal(getUser());
  const [rows, setRows] = createSignal([]);
  const [detail, setDetail] = createSignal(null);
  const [route, setRoute] = createSignal(readHash());
  const [error, setError] = createSignal("");
  const [notice, setNotice] = createSignal("");
  const [loading, setLoading] = createSignal(false);

  // 双车道台：左右车道、下一笔全部取自后端，不做任何前端排序。
  const [lanes, setLanes] = createSignal(null);
  const [claiming, setClaiming] = createSignal(false);
  let pollTimer = null;

  const [loginUser, setLoginUser] = createSignal("machinist");
  const [loginPass, setLoginPass] = createSignal("machine123456");

  const [toolCode, setToolCode] = createSignal("");
  const [offsetUm, setOffsetUm] = createSignal("");
  const [isUrgent, setIsUrgent] = createSignal(false);

  function goHome() {
    location.hash = "#/";
  }

  function goDesk() {
    location.hash = "#/desk";
  }

  function goDetail(id) {
    location.hash = `#/detail/${id}`;
  }

  async function loadRows() {
    setLoading(true);
    setError("");
    try {
      setRows(await fetchSubmissions());
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  async function loadDetail(id) {
    setLoading(true);
    setError("");
    try {
      setDetail(await fetchSubmission(id));
    } catch (e) {
      setError(e.message);
      setDetail(null);
    } finally {
      setLoading(false);
    }
  }

  async function loadLanes(silent = false) {
    if (!silent) setLoading(true);
    setError("");
    try {
      setLanes(await fetchLanes());
    } catch (e) {
      setError(e.message);
    } finally {
      if (!silent) setLoading(false);
    }
  }

  function startPolling() {
    stopPolling();
    pollTimer = setInterval(() => loadLanes(true), 2500);
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  onMount(() => {
    const onHash = () => setRoute(readHash());
    window.addEventListener("hashchange", onHash);
    onCleanup(() => {
      window.removeEventListener("hashchange", onHash);
      stopPolling();
    });
  });

  // 首次进入与每次路由切换都在此加载，避免与 onMount 双发请求。
  createEffect(() => {
    const r = route();
    if (!user()) return;
    if (r.name === "detail" && r.id) {
      stopPolling();
      loadDetail(r.id);
    } else if (r.name === "desk") {
      loadLanes();
      startPolling();
    } else {
      stopPolling();
      loadRows();
    }
  });

  async function handleLogin(e) {
    e.preventDefault();
    setError("");
    try {
      const data = await login(loginUser(), loginPass());
      setSession(data.token, {
        username: data.username,
        role: data.role,
        can_write: data.can_write,
        can_claim: data.can_claim,
      });
      setUser(getUser());
      goHome();
      // createEffect 已订阅 user()，setUser 后会自动按当前路由加载数据。
    } catch (err) {
      setError(err.message);
    }
  }

  function handleLogout() {
    clearSession();
    setUser(null);
    setRows([]);
    setDetail(null);
    setLanes(null);
    stopPolling();
    goHome();
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setNotice("");
    try {
      await createSubmission(toolCode(), offsetUm(), isUrgent());
      setToolCode("");
      setOffsetUm("");
      setIsUrgent(false);
      setNotice("交单成功，急补记号已随单锁死，事后不可改勾。");
      await loadRows();
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleClaim() {
    if (!user()?.can_claim) return;
    setClaiming(true);
    setError("");
    setNotice("");
    const nxt = lanes()?.next;
    try {
      // 只能按后端标出的下一笔认领；并发落败时后端返回 409。
      const got = await claimNext(nxt ? nxt.id : null);
      setNotice(`认领成功：${got.tool_code}（${got.is_urgent ? "急补" : "普通"}）已进入复核中。`);
      await loadLanes(true);
    } catch (err) {
      const why = err.status === 409 ? "另一笔已抢先认领（防双领）" : err.message;
      setError(`认领失败：${why}。请刷新双车道台查看最新下一笔。`);
      await loadLanes(true);
    } finally {
      setClaiming(false);
    }
  }

  return (
    <div class="page">
      <header class="topbar">
        <div class="brand">
          <h1>数控刀补复核台</h1>
          <p class="hint">
            急补车道压过普通车道，同车道按刀具编号升序；下一笔将领由后端统一排序函数给出，页面不私自另算。
          </p>
        </div>
        <Show when={user()}>
          <nav class="topnav">
            <a
              href="#/"
              class={route().name === "home" ? "active" : ""}
              onClick={(e) => {
                e.preventDefault();
                goHome();
              }}
            >
              复核总览
            </a>
            <a
              href="#/desk"
              class={route().name === "desk" ? "active" : ""}
              onClick={(e) => {
                e.preventDefault();
                goDesk();
              }}
            >
              双车道台
            </a>
          </nav>
        </Show>
      </header>

      <Show when={error()}>
        <div class="banner error">{error()}</div>
      </Show>
      <Show when={notice()}>
        <div class="banner ok">{notice()}</div>
      </Show>

      <Show
        when={user()}
        fallback={
          <section class="card">
            <h2>登录</h2>
            <form onSubmit={handleLogin} class="form">
              <label>
                用户名
                <input
                  value={loginUser()}
                  onInput={(e) => setLoginUser(e.currentTarget.value)}
                />
              </label>
              <label>
                密码
                <input
                  type="password"
                  value={loginPass()}
                  onInput={(e) => setLoginPass(e.currentTarget.value)}
                />
              </label>
              <button type="submit">进入系统</button>
            </form>
            <p class="hint">操作员 machinist / machine123456（可交单、交单时可勾急补）；复核员 auditor / audit123456（只能看记号与认领）</p>
          </section>
        }
      >
        <section class="card toolbar">
          <div>
            当前用户：<strong>{user().username}</strong>（{roleLabel[user().role] || user().role}）
            ｜ {user().can_write ? "可交单（交单时勾急补）" : "只读记号、可认领，不能交单/勾记号"}
          </div>
          <button type="button" class="ghost" onClick={handleLogout}>
            退出
          </button>
        </section>

        <Show when={route().name === "home"}>
          <Show when={user().can_write}>
            <section class="card">
              <h2>提交刀补</h2>
              <form onSubmit={handleSubmit} class="form inline">
                <label>
                  刀具编号
                  <input
                    placeholder="如 T01"
                    value={toolCode()}
                    onInput={(e) => setToolCode(e.currentTarget.value)}
                    required
                  />
                </label>
                <label>
                  刀补（微米）
                  <input
                    type="number"
                    value={offsetUm()}
                    onInput={(e) => setOffsetUm(e.currentTarget.value)}
                    required
                  />
                </label>
                <label class="checkline">
                  <span>
                    <input
                      type="checkbox"
                      class="checkbox"
                      checked={isUrgent()}
                      onChange={(e) => setIsUrgent(e.currentTarget.checked)}
                    />
                    急补记号
                  </span>
                  <small class="hint">仅交单此刻可勾，交后随单锁死，事后改勾无效</small>
                </label>
                <button type="submit">提交待复核</button>
              </form>
            </section>
          </Show>

          <section class="card">
            <div class="toolbar">
              <h2>复核总览（只显示记号，不可在此勾选）</h2>
              <button type="button" class="ghost" onClick={loadRows} disabled={loading()}>
                {loading() ? "刷新中…" : "刷新"}
              </button>
            </div>
            <table>
              <thead>
                <tr>
                  <th>车道</th>
                  <th>刀具</th>
                  <th>刀补 µm</th>
                  <th>状态</th>
                  <th>结论</th>
                  <th>认领人</th>
                  <th>提交时间</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                <For each={rows()}>
                  {(row) => (
                    <tr>
                      <td>
                        <UrgentTag urgent={row.is_urgent} />
                      </td>
                      <td>{row.tool_code}</td>
                      <td>{row.offset_um}</td>
                      <td>{statusLabel[row.status] || row.status}</td>
                      <td class={row.verdict === "合格" ? "pass" : row.verdict === "超差" ? "fail" : ""}>
                        {row.verdict || "—"}
                      </td>
                      <td>{row.claimed_by || "—"}</td>
                      <td>{new Date(row.created_at).toLocaleString()}</td>
                      <td>
                        <button type="button" class="ghost" onClick={() => goDetail(row.id)}>
                          详情
                        </button>
                      </td>
                    </tr>
                  )}
                </For>
              </tbody>
            </table>
            <Show when={!rows().length && !loading()}>
              <p class="hint">暂无记录</p>
            </Show>
          </section>
        </Show>

        <Show when={route().name === "desk"}>
          <section class="card">
            <div class="toolbar">
              <h2>双车道台</h2>
              <button type="button" class="ghost" onClick={() => loadLanes()} disabled={loading()}>
                {loading() ? "刷新中…" : "刷新"}
              </button>
            </div>

            <div class="next-bar">
              <Show
                when={lanes()?.next}
                fallback={<span class="hint">候审队列已空，暂无下一笔。</span>}
              >
                {(n) => (
                  <span>
                    下一笔将领：
                    <UrgentTag urgent={n().is_urgent} />
                    <strong> {n().tool_code}</strong>（刀补 {n().offset_um} µm）
                    —— 急补车道清空后才轮到普通车道，同车道按编号升序。
                  </span>
                )}
              </Show>
              <Show when={user().can_claim}>
                <button
                  type="button"
                  onClick={handleClaim}
                  disabled={claiming() || !lanes()?.next}
                >
                  {claiming() ? "认领中…" : "认领下一笔"}
                </button>
              </Show>
              <Show when={!user().can_claim}>
                <span class="hint">操作员账号只能查看，认领由复核员执行。</span>
              </Show>
            </div>

            <div class="lanes">
              <div class="lane urgent-lane">
                <h3>急补车道（左 · 优先认领）</h3>
                <For each={lanes()?.urgent || []}>
                  {(row) => (
                    <div class={"lane-item" + (lanes()?.next?.id === row.id ? " is-next" : "")}>
                      <span class="lane-code">{row.tool_code}</span>
                      <span class="hint">{row.offset_um} µm</span>
                      <Show when={lanes()?.next?.id === row.id}>
                        <span class="next-flag">下一笔将领</span>
                      </Show>
                    </div>
                  )}
                </For>
                <Show when={!(lanes()?.urgent || []).length}>
                  <p class="hint">急补车道空</p>
                </Show>
              </div>

              <div class="lane normal-lane">
                <h3>普通车道（右）</h3>
                <For each={lanes()?.normal || []}>
                  {(row) => (
                    <div class={"lane-item" + (lanes()?.next?.id === row.id ? " is-next" : "")}>
                      <span class="lane-code">{row.tool_code}</span>
                      <span class="hint">{row.offset_um} µm</span>
                      <Show when={lanes()?.next?.id === row.id}>
                        <span class="next-flag">下一笔将领</span>
                      </Show>
                    </div>
                  )}
                </For>
                <Show when={!(lanes()?.normal || []).length}>
                  <p class="hint">普通车道空</p>
                </Show>
              </div>
            </div>

            <h3 class="processing-title">复核中（已被认领）</h3>
            <table>
              <thead>
                <tr>
                  <th>车道</th>
                  <th>刀具</th>
                  <th>刀补 µm</th>
                  <th>认领人</th>
                </tr>
              </thead>
              <tbody>
                <For each={lanes()?.processing || []}>
                  {(row) => (
                    <tr>
                      <td>
                        <UrgentTag urgent={row.is_urgent} />
                      </td>
                      <td>{row.tool_code}</td>
                      <td>{row.offset_um}</td>
                      <td>{row.claimed_by || "—"}</td>
                    </tr>
                  )}
                </For>
              </tbody>
            </table>
            <Show when={!(lanes()?.processing || []).length}>
              <p class="hint">暂无复核中的单子</p>
            </Show>
          </section>
        </Show>

        <Show when={route().name === "detail"}>
          <section class="card">
            <div class="toolbar">
              <h2>刀补详情</h2>
              <button type="button" class="ghost" onClick={goHome}>
                返回总览
              </button>
            </div>
            <Show when={detail()} fallback={<p class="hint">{loading() ? "加载中…" : "未找到记录"}</p>}>
              {(d) => (
                <div class="detail-grid">
                  <p>
                    车道记号：<UrgentTag urgent={d().is_urgent} />
                    <small class="hint">（交单时锁定，不可改勾）</small>
                  </p>
                  <p>编号：{d().id}</p>
                  <p>刀具：{d().tool_code}</p>
                  <p>刀补 µm：{d().offset_um}</p>
                  <p>状态：{statusLabel[d().status] || d().status}</p>
                  <p class={d().verdict === "合格" ? "pass" : d().verdict === "超差" ? "fail" : ""}>
                    结论：{d().verdict || "—"}
                  </p>
                  <p>认领人：{d().claimed_by || "—"}</p>
                  <p>提交时间：{new Date(d().created_at).toLocaleString()}</p>
                  <p>
                    复核时间：
                    {d().reviewed_at ? new Date(d().reviewed_at).toLocaleString() : "—"}
                  </p>
                </div>
              )}
            </Show>
          </section>
        </Show>
      </Show>
    </div>
  );
}

export default App;
