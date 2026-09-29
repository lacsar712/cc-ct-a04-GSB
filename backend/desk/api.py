from datetime import datetime
from typing import Optional

from django.http import HttpRequest
from ninja import NinjaAPI, Schema
from ninja.errors import HttpError

from desk.auth_utils import bearer_auth, create_access_token, verify_password
from desk.models import OffsetSubmission, User
from desk.services import (
    CLAIMED,
    LOCKED,
    STALE,
    TAKEN,
    claim_next_submission,
    lane_snapshot,
    peek_next_pending,
    processing_submissions,
)

api = NinjaAPI(title="数控刀补复核台", version="1.1")


class HealthOut(Schema):
    status: str


class LoginIn(Schema):
    username: str
    password: str


class LoginOut(Schema):
    token: str
    username: str
    role: str
    can_write: bool
    can_claim: bool


class SubmissionIn(Schema):
    tool_code: str
    offset_um: int
    # 急补记号只在交单这一刻可勾，交后无任何修改接口，随单锁死。
    is_urgent: bool = False


class SubmissionOut(Schema):
    id: int
    tool_code: str
    offset_um: int
    is_urgent: bool
    status: str
    verdict: str
    created_at: datetime
    reviewed_at: Optional[datetime]
    claimed_by: Optional[str]


class ClaimIn(Schema):
    # 页面在双车道台上看到的「下一笔」id；服务端会校验它仍是排序首位。
    target_id: Optional[int] = None


class LanesOut(Schema):
    next: Optional[SubmissionOut]
    urgent: list[SubmissionOut]
    normal: list[SubmissionOut]
    processing: list[SubmissionOut]


def _to_out(row: OffsetSubmission) -> SubmissionOut:
    return SubmissionOut(
        id=row.id,
        tool_code=row.tool_code,
        offset_um=row.offset_um,
        is_urgent=row.is_urgent,
        status=row.status,
        verdict=row.verdict or "",
        created_at=row.created_at,
        reviewed_at=row.reviewed_at,
        claimed_by=row.claimed_by.username if row.claimed_by_id else None,
    )


@api.get("/health", response=HealthOut)
def health(request: HttpRequest):
    return {"status": "ok"}


@api.post("/auth/login", response=LoginOut)
def login(request: HttpRequest, body: LoginIn):
    try:
        user = User.objects.get(username=body.username)
    except User.DoesNotExist:
        raise HttpError(401, "用户名或密码错误")
    if not verify_password(body.password, user.password):
        raise HttpError(401, "用户名或密码错误")
    token = create_access_token(user)
    return {
        "token": token,
        "username": user.username,
        "role": user.role,
        "can_write": user.can_write,
        "can_claim": user.can_claim,
    }


@api.get("/submissions", response=list[SubmissionOut], auth=bearer_auth)
def list_submissions(request: HttpRequest):
    rows = OffsetSubmission.objects.select_related("claimed_by").all()[:200]
    return [_to_out(r) for r in rows]


# 注意：静态路径必须在 /submissions/{submission_id} 之前注册，
# 否则 "claim" 会被当作 submission_id 匹配，POST 落到只允许 GET 的路由上变 405。
@api.post("/submissions/claim", response=SubmissionOut, auth=bearer_auth)
def claim_submission(request: HttpRequest, body: ClaimIn):
    """复核员认领候审单。并发双领时一笔成功、另一笔拿到 409 明确失败。"""
    user: User = request.auth
    if not user.can_claim:
        raise HttpError(403, "只有复核员能认领候审单")

    result = claim_next_submission(user=user, target_id=body.target_id)
    if result.status == CLAIMED:
        return _to_out(result.submission)

    status_code = 409 if result.status in (LOCKED, TAKEN, STALE) else 404
    # LOCKED 锁竞争 / TAKEN 已被领走 / STALE 排序过期 / EMPTY 队列空
    raise HttpError(status_code, result.detail)


@api.get("/submissions/{submission_id}", response=SubmissionOut, auth=bearer_auth)
def get_submission(request: HttpRequest, submission_id: int):
    try:
        row = OffsetSubmission.objects.get(pk=submission_id)
    except OffsetSubmission.DoesNotExist:
        raise HttpError(404, "刀补记录不存在")
    return _to_out(row)


@api.post("/submissions", response=SubmissionOut, auth=bearer_auth)
def create_submission(request: HttpRequest, body: SubmissionIn):
    user: User = request.auth
    if not user.can_write:
        raise HttpError(403, "复核员账号只能看记号与认领，不能交单")
    tool_code = body.tool_code.strip()
    if not tool_code:
        raise HttpError(400, "刀具编号不能为空")
    row = OffsetSubmission.objects.create(
        tool_code=tool_code,
        offset_um=body.offset_um,
        is_urgent=body.is_urgent,
        submitted_by=user,
        status=OffsetSubmission.Status.PENDING,
    )
    # is_urgent 创建即锁死：模型 editable=False，且系统不提供改勾接口。
    return _to_out(row)


@api.get("/desk/lanes", response=LanesOut, auth=bearer_auth)
def desk_lanes(request: HttpRequest):
    """双车道台数据：急补车道 / 普通车道 / 下一笔将领，全部由服务端按
    desk.services 的唯一排序算出，页面只负责原样展示，不得自行另算。"""
    urgent, normal = lane_snapshot()
    nxt = peek_next_pending()
    return {
        "next": _to_out(nxt) if nxt else None,
        "urgent": [_to_out(r) for r in urgent],
        "normal": [_to_out(r) for r in normal],
        "processing": [_to_out(r) for r in processing_submissions()],
    }
