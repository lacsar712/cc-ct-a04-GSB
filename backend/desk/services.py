"""候审队列的唯一排序与认领逻辑。

双车道台的「下一笔将领」预览与后台 worker / 复核员的实际认领都只能走
本模块的同一套函数，任何页面或进程不得自行另算下一笔。

排序规则（QUEUE_ORDER）：
  1. 急补车道整体压过普通车道；
  2. 同一车道内按刀具编号升序；
  3. 编号相同按交单先后（主键 id）。
"""

from dataclasses import dataclass
from typing import Optional

from django.conf import settings
from django.db import DatabaseError, transaction
from django.utils import timezone

from desk.models import OffsetSubmission, User

# 全系统唯一的候审排序定义，改顺序只准改这里。
QUEUE_ORDER = ("-is_urgent", "tool_code", "id")


def pending_submissions():
    """候审队列（待复核），已按 QUEUE_ORDER 排好。"""
    return (
        OffsetSubmission.objects.filter(status=OffsetSubmission.Status.PENDING)
        .order_by(*QUEUE_ORDER)
    )


def peek_next_pending() -> Optional[OffsetSubmission]:
    """下一笔将领是谁——只看不锁。"""
    return pending_submissions().first()


def lane_snapshot() -> tuple[list[OffsetSubmission], list[OffsetSubmission]]:
    """双车道台数据：(左=急补车道, 右=普通车道)，顺序同 QUEUE_ORDER。"""
    queue = list(pending_submissions())
    urgent = [row for row in queue if row.is_urgent]
    normal = [row for row in queue if not row.is_urgent]
    return urgent, normal


def processing_submissions():
    """已进入复核中的单子（含谁认领的），最新认领在前。"""
    return (
        OffsetSubmission.objects.filter(status=OffsetSubmission.Status.PROCESSING)
        .select_related("claimed_by")
        .order_by("-id")
    )


# 认领结果状态码
CLAIMED = "claimed"   # 成功：单子已被本笔认领进复核中
LOCKED = "locked"     # 明确失败：同一候审单正被另一笔认领锁定
TAKEN = "taken"       # 明确失败：该单已不在候审队列（已被领走/已完成）
STALE = "stale"       # 明确失败：页面看到的下一笔已不是服务端排序首位
EMPTY = "empty"       # 明确失败：候审队列已空


@dataclass
class ClaimResult:
    status: str
    submission: Optional[OffsetSubmission] = None
    detail: str = ""


def claim_next_submission(
    user: Optional[User],
    target_id: Optional[int] = None,
) -> ClaimResult:
    """认领候审单。

    user 为复核员时记录归属；后台 worker 传 None。
    target_id 为页面在双车道台上看到的「下一笔」id，服务端会校验它仍是
    排序首位，否则返回 STALE，杜绝页面拿着旧排序乱领。

    并发安全：行级锁 + NOWAIT。两笔同时认领同一候审单时，一笔拿到锁进入
    复核中，另一笔立即拿到 LOCKED 明确失败，绝无双领。
    """
    with transaction.atomic():
        candidates = pending_submissions().select_for_update(nowait=True)
        if target_id is not None:
            candidates = candidates.filter(pk=target_id)
        try:
            submission = candidates.first()
        except DatabaseError as exc:
            if _is_lock_not_available(exc):
                return ClaimResult(
                    LOCKED,
                    detail="该候审单正被另一笔认领锁定，本笔认领失败",
                )
            raise

        if submission is None:
            return _classify_missing(target_id)

        # 持锁后再核对一次：页面指认的单子必须仍是服务端排序首位。
        head_id = pending_submissions().values_list("pk", flat=True).first()
        if target_id is not None and head_id != submission.pk:
            return ClaimResult(
                STALE,
                detail="排序已变化，页面显示的下一笔已失效，请刷新双车道台",
            )

        submission.status = OffsetSubmission.Status.PROCESSING
        submission.claimed_by = user
        submission.save(update_fields=["status", "claimed_by"])
        return ClaimResult(CLAIMED, submission=submission, detail="认领成功")


def _classify_missing(target_id: Optional[int]) -> ClaimResult:
    if target_id is None:
        return ClaimResult(EMPTY, detail="候审队列已空，没有可认领的刀补单")
    row = OffsetSubmission.objects.filter(pk=target_id).first()
    if row is None:
        return ClaimResult(TAKEN, detail="该刀补单不存在或已离开复核台")
    if row.status != OffsetSubmission.Status.PENDING:
        who = row.claimed_by.username if row.claimed_by_id else "他人"
        return ClaimResult(
            TAKEN,
            detail=f"该单已被{who}领走（{row.get_status_display()}），本笔认领失败",
        )
    # 仍在候审却没被选中（理论上不会发生：按主键过滤必然命中）。
    return ClaimResult(STALE, detail="该单当前不在可认领位置，请刷新双车道台")


def _is_lock_not_available(exc: DatabaseError) -> bool:
    inner = getattr(exc, "__cause__", None)
    sqlstate = getattr(inner, "sqlstate", None) or getattr(exc, "sqlstate", None)
    # PostgreSQL 55P03 = lock_not_available（SELECT ... FOR UPDATE NOWAIT 抢锁失败）
    return sqlstate == "55P03"


def evaluate_verdict(offset_um: int) -> str:
    if abs(offset_um) <= settings.OFFSET_TOLERANCE_UM:
        return OffsetSubmission.Verdict.PASS
    return OffsetSubmission.Verdict.FAIL


def apply_verdict(submission: OffsetSubmission) -> None:
    submission.verdict = evaluate_verdict(submission.offset_um)
    submission.status = OffsetSubmission.Status.DONE
    submission.reviewed_at = timezone.now()
    submission.save(
        update_fields=["verdict", "status", "reviewed_at"],
    )


def finish_next_processing() -> bool:
    """推进一条复核中的单子出结论（人工认领与 worker 自领的都由此收口）。"""
    row = (
        OffsetSubmission.objects.filter(status=OffsetSubmission.Status.PROCESSING)
        .order_by("id")
        .first()
    )
    if row is None:
        return False
    apply_verdict(row)
    return True
