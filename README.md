# 数控刀补复核台

操作员交单（可勾选**急补**，记号随单锁死，事后改勾无效）进入待复核；复核员在**双车道台**（左急补、右普通）按后台统一排序认领下一笔，行锁保证并发不双领，随后交「合格/超差」结论。

## 排序与认领规则（核心）

1. **先清急补待复核，再碰普通**；同一车道内按交单编号 `id` 升序。
2. 队列总览的「下一笔将领」与认领进程**共用同一个排序函数**（`desk.services.CLAIM_ORDERING = ("-is_urgent", "id")`），页面禁止私自另算下一笔。
3. 认领在数据库事务内 `SELECT ... FOR UPDATE SKIP LOCKED` + 条件更新兜底：两人几乎同时认领时只许一笔进入「复核中」，另一笔收到明确失败（HTTP 409），不得双领。
4. 急补记号仅在交单（`POST /submissions`）时写入；系统不提供任何改勾入口。

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
| machinist | machine123456 | 可交单（可勾急补）；双车道台只能看，不能认领/交结论 |
| auditor | audit123456 | 双车道台认领、复核交结论；无交单表单 |

## 启动

```bash
docker compose up --build
```

健康检查：`GET http://localhost:8196/api/health` → `{"status":"ok"}`

## 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/login` | 登录换 JWT |
| GET | `/api/submissions` | 复核总览（只读，勾选不算） |
| POST | `/api/submissions` | 操作员交单，body 可带 `is_urgent: true` |
| GET | `/api/desk/queue` | 双车道台：`next` / `urgent[]` / `normal[]` / `processing[]` |
| POST | `/api/desk/claim` | 复核员认领下一笔（并发失败返回 409） |
| POST | `/api/desk/review/{id}` | 认领人交结论「合格」/「超差」 |

## 验收场景

1. machinist **先交普通「丙刀」**，再交**急补「丁刀」**。
2. 打开「双车道台」：急补车道（左）有丁刀、普通车道（右）有丙刀；顶部「下一笔将领」显示 **#丁刀**。
3. auditor 点「认领下一笔」：实际拿到的必须是**丁刀**（而不是先交的丙刀）。
4. 对丁刀交结论后再认领，下一笔才轮到**丙刀**。
5. 两个 auditor 会话几乎同时点认领同一候审单：一笔进入「复核中」，另一笔明确提示认领失败，绝不出现双领。
6. machinist 打开双车道台能看到记号与下一笔，但没有认领/交结论按钮；总览列表中的勾选仅为展示，不改变排序。
