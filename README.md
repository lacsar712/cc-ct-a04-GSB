# 数控刀补复核台

操作员提交刀具编号与刀补微米值，**交单时可勾「急补」记号**（记号随单锁死，事后改勾无效）。候审单排进**双车道台**：急补车道整体优先、清空后才轮到普通车道，同车道按刀具编号升序。复核员在双车道台按服务端给出的「下一笔将领」认领；认领用 PostgreSQL 行锁（`select_for_update(nowait=True)`）保证两人同时抢同一单时**只许一笔进入复核中，另一笔明确失败（409），绝不双领**。worker 负责把「复核中」的单子按绝对值是否不超过 12 微米给出「合格」或「超差」。

## 排序与认领的唯一真相源

- 排序定义只在 `backend/desk/services.py` 的 `QUEUE_ORDER = ("-is_urgent", "tool_code", "id")`。
- 双车道台预览（`peek_next_pending` / `lane_snapshot`）、复核员认领（`claim_next_submission`）、后台 worker **共用同一套函数**；前端只原样展示 `GET /api/desk/lanes` 的结果，**禁止私自另算下一笔**。
- 认领时前端把看到的 `next.id` 作为 `target_id` 回传，服务端持锁后再校验它仍是排序首位，排序过期返回 `409 STALE`。

## 技术栈

| 层 | 选型 |
|----|------|
| 后端 | Django 5 + django-ninja（ASGI / uvicorn） |
| 前端 | SolidJS + Vite，nginx 反代 `/api` |
| 数据库 | PostgreSQL 16 |
| 鉴权 | JWT（python-jose），令牌存浏览器 localStorage |

## 端口

| 服务 | 地址 |
|------|------|
| 页面 | http://localhost:3196 |
| 接口 | http://localhost:8196 |
| PostgreSQL | localhost:54396（库名 `cncoffset`） |

## 账号

| 用户 | 密码 | 权限 |
|------|------|------|
| machinist | machine123456 | 可交刀补单，交单时可勾「急补」 |
| auditor | audit123456 | 只能看记号 / 双车道台 / 下一笔，不能勾记号、不能交单，可认领 |

## 启动

```bash
cd projects/17-cnc-tool-offset-desk
docker compose up --build
```

健康检查：`GET http://localhost:8196/api/health` → `{"status":"ok"}`

## 验收

1. machinist 登录后，种子数据应显示刀具 T01 合格（刀补 5 µm）、T09 超差（刀补 20 µm）。
2. machinist 交单时可勾「急补」；交单后任何页面/接口都无法改勾（无 PATCH/PUT，模型 `is_urgent` 为 `editable=False`）。
3. **急补插队**：先交普通「丙刀」，再交急补「丁刀」→ 双车道台「下一笔将领」必须是丁刀（左急补车道队首）；复核员点认领实拿丁刀，下一笔切到丙刀；再领才实拿丙刀。
4. **并发防双领**：两名复核员几乎同时认领同一候审单 → 一笔 200 进入「复核中」并记录认领人，另一笔收到 `409 该候审单正被另一笔认领锁定，本笔认领失败`，绝不双领。
5. auditor 登录后能看到急补/普通记号、双车道台、下一笔，可认领；但交单接口 403、页面没有勾选框。
6. worker（`WORKER_AUTO_CLAIM=0`）只把「复核中」收口为「已完成」并给结论，不与复核员抢候审单。

后端测试（含真实多线程抢锁与 HTTP 丙丁流程）：

```bash
cd backend
python manage.py test desk
```

## 主要接口

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| POST | `/api/auth/login` | 公开 | 返回 `can_write` / `can_claim` |
| POST | `/api/submissions` | 操作员 | 交单，body 可带 `is_urgent`（仅此刻可勾） |
| GET | `/api/desk/lanes` | 登录 | `next` + `urgent[]`（左）+ `normal[]`（右）+ `processing[]`，服务端统一排序 |
| POST | `/api/submissions/claim` | 复核员 | body `{target_id}`，抢锁失败/排序过期返回 409 |

## 目录

```text
backend/          Django 工程（config/、desk/、worker.py）
frontend/         SolidJS 单页
docker-compose.yml
PRD.md
```
