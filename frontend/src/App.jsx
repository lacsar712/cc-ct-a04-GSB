import { createSignal, onCleanup, onMount, Show, For, createEffect } from "solid-js";
import {
  clearSession,
  createSubmission,
  fetchSubmission,
  fetchSubmissions,
  fetchDeskQueue,
  claimNext,
  reviewSubmission,
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

function UrgentBadge({ urgent }) {
  return (
    <Show when={urgent}>
      <span class="badge urgent">急补</span>
    </Show>
  );
}

function LaneCard(props) {
  return (
    <section class={`card lane ${props.urgent ? "lane-urgent" : "lane-normal"}`}>
      <h3>
        {props.urgent ? "急补车道" : "普通车道"}
        <span class="count">{props.items.length} 笔候审</span>
      </h3>
      <Show when={props.items.length} fallback={<p class="hint">车道暂无候审单</p>}>
        <ul class="queue-list">
          <For each={props.items}>
            {(row) => (
              <li classList={{ next: props.nextId === row.id }}>
                <span class="queue-main">
                  #{row.id} {row.tool_code}
                  <UrgentBadge urgent={row.is_urgent} />
                  <Show when={props.nextId === row.id}>
                    <span class="next-tag">下一笔将领</span>
                  </Show>
                </span>
                <span class="queue-sub">{row.offset_um}µm</span>
              </li>
            )}
          </For>
        </ul>
      </Show>
    </section>
  );
}

function App() {
  const [user, setUser] = createSignal(getUser());
  const [rows, setRows] = createSignal([]);
  const [queue, setQueue] = createSignal({ next: null, urgent: [], normal: [], processing: [] });
  const [detail, setDetail] = createSignal(null);
  const [route, setRoute] = createSignal(readHash());
  const [error, setError] = createSignal("");
  const [loading, setLoading] = createSignal(false);

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
      const data = await fetchSubmissions();
      setRows(data);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  async function loadQueue(showLoading = true) {
    if (showLoading) setLoading(true);
    try {
      setQueue(await fetchDeskQueue());
    } catch (e) {
      if (showLoading) setError(e.message);
    } finally {
      if (showLoading) setLoading(false);
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

  let pollTimer = null;

  onMount(() => {
    const onHash = () => setRoute(readHash());
    window.addEventListener("hashchange", onHash);
    onCleanup(() => window.removeEventListener("hashchange", onHash));
  });

  createEffect(() => {
    const r = route();
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
    if (!user()) return;
    if (r.name === "detail" && r.id) loadDetail(r.id);
    else if (r.name === "home") loadRows();
    else if (r.name === "desk") {
      loadQueue();
      // 双车道台近实时刷新，仅用于展示；认领裁决仍以服务端行锁为准
      pollTimer = setInterval(() => loadQueue(false), 4000);
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
      });
      setUser(getUser());
      goHome();
      await loadRows();
    } catch (err) {
      setError(err.message);
    }
  }

  function handleLogout() {
    clearSession();
    setUser(null);
    setRows([]);
    setDetail(null);
    goHome();
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    try {
      await createSubmission(toolCode(), offsetUm(), isUrgent());
      setToolCode("");
      setOffsetUm("");
      setIsUrgent(false);
      await loadRows();
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleClaim() {
    setError("");
    try {
      await claimNext();
      await loadQueue();
    } catch (err) {
      // 并发抢同一单：失败者必须看到明确失败，绝不双领
      setError(`认领失败：${err.message}`);
      await loadQueue(false);
    }
  }

  async function handleReview(id, verdict) {
    setError("");
    try {
      await reviewSubmission(id, verdict);
      await loadQueue();
    } catch (err) {
      setError(err.message);
    }
  }

  const isAuditor = () => user()?.role === "auditor";

  return (
    <div class="page">
      <header class="topbar">
        <div class="brand">
          <h1>数控刀补复核台</h1>
          <p class="hint">
            急补优先于普通认领；同一车道按编号升序。下一笔由后台统一排序给出，页面不得私自另算。
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
            <p class="hint">操作员 machinist / machine123456；复核员 auditor / audit123456</p>
          </section>
        }
      >
        <section class="card toolbar">
          <div>
            当前用户：<strong>{user().username}</strong>（{roleLabel[user().role] || user().role}）
          </div>
          <button type="button" class="ghost" onClick={handleLogout}>
            退出
          </button>
        </section>

        <Show when={route().name === "home"}>
          <Show when={user().can_write}>
            <section class="card">
              <h2>提交刀补（交单）</h2>
              <form onSubmit={handleSubmit} class="form inline">
                <label>
                  刀具编号
                  <input
                    placeholder="如 丙刀 / T03"
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
                <label class="checkbox-line">
                  <span>
                    <input
                      type="checkbox"
                      checked={isUrgent()}
                      onChange={(e) => setIsUrgent(e.currentTarget.checked)}
                    />
                    <strong class="urgent-text">急补</strong>
                  </span>
                  <small class="hint">记号随单锁死，交单后改勾无效</small>
                </label>
                <button type="submit">提交待复核</button>
              </form>
            </section>
          </Show>

          <section class="card">
            <div class="toolbar">
              <h2>复核列表（总览只读，勾选不算）</h2>
              <button type="button" class="ghost" onClick={loadRows} disabled={loading()}>
                {loading() ? "刷新中…" : "刷新"}
              </button>
            </div>
            <table>
              <thead>
                <tr>
                  <th>编号</th>
                  <th>刀具</th>
                  <th>记号</th>
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
                    <tr classList={{ "row-urgent": row.is_urgent }}>
                      <td>#{row.id}</td>
                      <td>{row.tool_code}</td>
                      <td>
                        <UrgentBadge urgent={row.is_urgent} />
                        <Show when={!row.is_urgent}>普通</Show>
                      </td>
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
          <section class="card next-card">
            <div class="toolbar">
              <h2>双车道台 · 下一笔将领</h2>
              <button type="button" class="ghost" onClick={() => loadQueue()} disabled={loading()}>
                {loading() ? "刷新中…" : "刷新"}
              </button>
            </div>
            <Show
              when={queue().next}
              fallback={<p class="hint">候审队列已清空，没有下一笔。</p>}
            >
              {(n) => (
                <div class="next-body">
                  <div>
                    <UrgentBadge urgent={n().is_urgent} />
                    <Show when={!n().is_urgent}>
                      <span class="badge normal">普通</span>
                    </Show>
                    <strong class="next-tool">
                      #{n().id} {n().tool_code}
                    </strong>
                    <span class="hint">（{n().offset_um}µm）</span>
                    <p class="hint">
                      先清急补待复核，再按普通编号升序；此判定来自后台同一排序函数。
                    </p>
                  </div>
                  <Show when={isAuditor()} fallback={<p class="hint">仅复核员可认领</p>}>
                    <button type="button" onClick={handleClaim}>
                      认领下一笔
                    </button>
                  </Show>
                </div>
              )}
            </Show>
          </section>

          <div class="lanes">
            <LaneCard urgent items={queue().urgent} nextId={queue().next?.id} />
            <LaneCard urgent={false} items={queue().normal} nextId={queue().next?.id} />
          </div>

          <section class="card">
            <h2>复核中</h2>
            <Show when={queue().processing.length} fallback={<p class="hint">暂无复核中单据</p>}>
              <table>
                <thead>
                  <tr>
                    <th>编号</th>
                    <th>刀具</th>
                    <th>记号</th>
                    <th>刀补 µm</th>
                    <th>认领人</th>
                    <th>交结论</th>
                  </tr>
                </thead>
                <tbody>
                  <For each={queue().processing}>
                    {(row) => (
                      <tr>
                        <td>#{row.id}</td>
                        <td>{row.tool_code}</td>
                        <td>
                          <UrgentBadge urgent={row.is_urgent} />
                          <Show when={!row.is_urgent}>普通</Show>
                        </td>
                        <td>{row.offset_um}</td>
                        <td>{row.claimed_by || "—"}</td>
                        <td>
                          <Show
                            when={isAuditor() && row.claimed_by === user().username}
                            fallback={<span class="hint">仅认领人可交结论</span>}
                          >
                            <button
                              type="button"
                              class="verdict pass-btn"
                              onClick={() => handleReview(row.id, "合格")}
                            >
                              合格
                            </button>
                            <button
                              type="button"
                              class="verdict fail-btn"
                              onClick={() => handleReview(row.id, "超差")}
                            >
                              超差
                            </button>
                          </Show>
                        </td>
                      </tr>
                    )}
                  </For>
                </tbody>
              </table>
            </Show>
            <Show when={!isAuditor()}>
              <p class="hint">复核员可在此认领并交结论；当前账号只能查看记号与排序。</p>
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
                  <p>编号：#{d().id}</p>
                  <p>
                    记号：
                    <UrgentBadge urgent={d().is_urgent} />
                    <Show when={!d().is_urgent}>普通</Show>
                  </p>
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
