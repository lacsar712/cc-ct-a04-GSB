from django.conf import settings
from django.db import transaction
from django.utils import timezone

from desk.models import OffsetSubmission, User

# 双车道台排序：先清空急补车道（待复核），再走普通车道；
# 同一车道内按交单编号升序（id 即交单先后，含同刻提交）。
# 队列总览的“下一笔将领”与认领进程必须共用本函数，页面不得私自另算。
CLAIM_ORDERING = ("-is_urgent", "id")


class AlreadyClaimed(Exception):
    """目标单已被他人认领：并发认领只许一笔进入审中，另一笔明确失败。"""


def evaluate_verdict(offset_um: int) -> str:
    if abs(offset_um) <= settings.OFFSET_TOLERANCE_UM:
        return OffsetSubmission.Verdict.PASS
    return OffsetSubmission.Verdict.FAIL


def pending_queryset():
    return OffsetSubmission.objects.filter(status=OffsetSubmission.Status.PENDING)


def get_queue_overview() -> dict:
    """双车道台总览：左急补、右普通，并标出全局下一笔将领是谁。

    next 与 claim_next_submission 共用同一排序，保证“看到的下一笔”
    与“实际领到的”永远一致。
    """
    pending = list(pending_queryset().order_by(*CLAIM_ORDERING))
    urgent = [s for s in pending if s.is_urgent]
    normal = [s for s in pending if not s.is_urgent]
    return {
        "next": pending[0] if pending else None,
        "urgent": urgent,
        "normal": normal,
        "processing": list(
            OffsetSubmission.objects.filter(
                status=OffsetSubmission.Status.PROCESSING
            ).order_by(*CLAIM_ORDERING)
        ),
    }


def claim_next_submission(auditor: User) -> OffsetSubmission:
    """认领下一笔将领。

    先按统一排序定出 next 的 id（与 get_queue_overview 同源），再按主键
    行锁该行：两人几乎同时抢同一候审单时，一人成功进入审中，另一人
    SKIP LOCKED 落空 -> 明确失败，绝不双领，也不会悄悄顺延领走别的单。
    """
    with transaction.atomic():
        next_id = (
            pending_queryset()
            .order_by(*CLAIM_ORDERING)
            .values_list("id", flat=True)
            .first()
        )
        if next_id is None:
            raise AlreadyClaimed("没有待复核单据可认领")
        submission = (
            OffsetSubmission.objects.select_for_update(skip_locked=True)
            .filter(pk=next_id)
            .first()
        )
        if submission is None:
            # 该行正被并发认领事务锁住
            raise AlreadyClaimed("该单据正被他人认领，请勿重复认领")
        if submission.status != OffsetSubmission.Status.PENDING:
            raise AlreadyClaimed("该单据已被他人认领，请改领下一笔")
        # 条件更新兜底：只允许待复核 → 复核中状态翻转一次
        updated = (
            OffsetSubmission.objects.filter(
                pk=submission.pk, status=OffsetSubmission.Status.PENDING
            ).update(status=OffsetSubmission.Status.PROCESSING, claimed_by=auditor)
        )
        if updated == 0:
            raise AlreadyClaimed("该单据已被他人认领，请改领下一笔")
        submission.status = OffsetSubmission.Status.PROCESSING
        submission.claimed_by = auditor
    return submission


def apply_verdict(submission: OffsetSubmission, verdict: str | None = None) -> None:
    submission.verdict = verdict or evaluate_verdict(submission.offset_um)
    submission.status = OffsetSubmission.Status.DONE
    submission.reviewed_at = timezone.now()
    submission.save(
        update_fields=["verdict", "status", "reviewed_at"],
    )
