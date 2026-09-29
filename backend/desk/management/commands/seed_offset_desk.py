from django.core.management.base import BaseCommand
from django.utils import timezone

from desk.auth_utils import hash_password
from desk.models import OffsetSubmission, User


class Command(BaseCommand):
    help = "创建默认账号与种子刀补记录"

    def handle(self, *args, **options):
        machinist, _ = User.objects.update_or_create(
            username="machinist",
            defaults={
                "role": User.Role.MACHINIST,
                "password": hash_password("machine123456"),
                "is_active": True,
            },
        )
        User.objects.update_or_create(
            username="auditor",
            defaults={
                "role": User.Role.AUDITOR,
                "password": hash_password("audit123456"),
                "is_active": True,
            },
        )

        now = timezone.now()
        # (刀具, 刀补, 结论, 急补记号, 状态)
        seeds = [
            ("T01", 5, OffsetSubmission.Verdict.PASS, False, OffsetSubmission.Status.DONE),
            ("T09", 20, OffsetSubmission.Verdict.FAIL, False, OffsetSubmission.Status.DONE),
            ("甲刀", 6, "", True, OffsetSubmission.Status.PENDING),
            ("乙刀", 9, "", False, OffsetSubmission.Status.PENDING),
        ]
        for tool_code, offset_um, verdict, is_urgent, status in seeds:
            defaults = {
                "status": status,
                "is_urgent": is_urgent,
                "submitted_by": machinist,
                "claimed_by": None,
                "verdict": verdict if status == OffsetSubmission.Status.DONE else "",
                "reviewed_at": now if status == OffsetSubmission.Status.DONE else None,
            }
            OffsetSubmission.objects.update_or_create(
                tool_code=tool_code,
                offset_um=offset_um,
                defaults=defaults,
            )

        self.stdout.write(self.style.SUCCESS("seed_offset_desk 完成"))
