from datetime import datetime
from typing import Optional

from django.http import HttpRequest
from ninja import NinjaAPI, Schema
from ninja.errors import HttpError

from desk import services
from desk.auth_utils import bearer_auth, create_access_token, verify_password
from desk.models import OffsetSubmission, User

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


class SubmissionIn(Schema):
    tool_code: str
    offset_um: int
    # 操作员交单时勾选急补；记号随单锁死，接口不接受事后改勾
    is_urgent: bool = False


class SubmissionOut(Schema):
    id: int
    tool_code: str
    offset_um: int
    is_urgent: bool
    status: str
    verdict: str
    claimed_by: Optional[str]
    created_at: datetime
    reviewed_at: Optional[datetime]


class QueueOut(Schema):
    next: Optional[SubmissionOut]
    urgent: list[SubmissionOut]
    normal: list[SubmissionOut]
    processing: list[SubmissionOut]


class ReviewIn(Schema):
    # 复核员交复核结论；空则按公差自动判定
    verdict: Optional[str] = None


def _to_out(row: OffsetSubmission) -> SubmissionOut:
    return SubmissionOut(
        id=row.id,
        tool_code=row.tool_code,
        offset_um=row.offset_um,
        is_urgent=row.is_urgent,
        status=row.status,
        verdict=row.verdict or "",
        claimed_by=row.claimed_by.username if row.claimed_by else None,
        created_at=row.created_at,
        reviewed_at=row.reviewed_at,
    )


def _require_auditor(user: User) -> None:
    if user.role != User.Role.AUDITOR:
        raise HttpError(403, "仅复核员可在双车道台认领与复核")


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
    }


@api.get("/submissions", response=list[SubmissionOut], auth=bearer_auth)
def list_submissions(request: HttpRequest):
    rows = OffsetSubmission.objects.all().order_by("-id")[:200]
    return [_to_out(r) for r in rows]


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
        raise HttpError(403, "当前账号只读，不能提交刀补")
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
    return _to_out(row)


@api.get("/desk/queue", response=QueueOut, auth=bearer_auth)
def desk_queue(request: HttpRequest):
    """双车道台：左急补、右普通，next 标出下一笔将领（只读总览）。"""
    overview = services.get_queue_overview()
    return {
        "next": _to_out(overview["next"]) if overview["next"] else None,
        "urgent": [_to_out(r) for r in overview["urgent"]],
        "normal": [_to_out(r) for r in overview["normal"]],
        "processing": [_to_out(r) for r in overview["processing"]],
    }


@api.post("/desk/claim", response=SubmissionOut, auth=bearer_auth)
def desk_claim_next(request: HttpRequest):
    """认领下一笔将领。排序与 /desk/queue 的 next 完全同源。"""
    user: User = request.auth
    _require_auditor(user)
    try:
        row = services.claim_next_submission(user)
    except services.AlreadyClaimed as exc:
        # 并发抢同一单：失败者拿到明确 409，不得双领
        raise HttpError(409, str(exc))
    return _to_out(row)


@api.post("/desk/review/{submission_id}", response=SubmissionOut, auth=bearer_auth)
def desk_review(request: HttpRequest, submission_id: int, body: ReviewIn):
    """复核员对自己审中的单据交结论。"""
    user: User = request.auth
    _require_auditor(user)
    try:
        row = OffsetSubmission.objects.get(pk=submission_id)
    except OffsetSubmission.DoesNotExist:
        raise HttpError(404, "刀补记录不存在")
    if row.status != OffsetSubmission.Status.PROCESSING:
        raise HttpError(409, "该单据不在复核中，无法提交结论")
    if row.claimed_by_id != user.id:
        raise HttpError(403, "该单据由其他复核员认领，不能代交")
    if body.verdict:
        if body.verdict not in OffsetSubmission.Verdict.values:
            raise HttpError(400, "结论只能是「合格」或「超差」")
        verdict = body.verdict
    else:
        verdict = services.evaluate_verdict(row.offset_um)
    services.apply_verdict(row, verdict)
    return _to_out(row)
