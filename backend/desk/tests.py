"""双车道台 / 急补插队 / 并发认领测试。

并发用例使用 TransactionTestCase：行要真正提交，两个线程（两条数据库
连接）才能同时看到同一候审单并真刀真枪抢行锁。
"""

import threading
from datetime import timedelta

from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from desk.auth_utils import create_access_token
from desk.models import OffsetSubmission, User
from desk.services import (
    CLAIMED,
    LOCKED,
    STALE,
    TAKEN,
    claim_next_submission,
    lane_snapshot,
    peek_next_pending,
    pending_submissions,
)


def make_user(username, role):
    user, _ = User.objects.get_or_create(
        username=username, defaults={"role": role, "is_active": True}
    )
    return user


def submit(tool_code, is_urgent=False, offset_um=5):
    return OffsetSubmission.objects.create(
        tool_code=tool_code,
        offset_um=offset_um,
        is_urgent=is_urgent,
        status=OffsetSubmission.Status.PENDING,
    )


def auth_header(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {create_access_token(user)}"}


class QueueOrderTests(TransactionTestCase):
    def test_normal_lane_orders_by_tool_code_asc(self):
        # 与提交先后无关，普通车道严格按刀具编号升序。
        submit("T03")
        submit("T01")
        submit("T02")
        order = list(pending_submissions().values_list("tool_code", flat=True))
        self.assertEqual(order, ["T01", "T02", "T03"])

    def test_urgent_lane_clears_before_normal(self):
        submit("T05", is_urgent=True)
        submit("T01", is_urgent=False)
        submit("T03", is_urgent=True)
        order = list(pending_submissions().values_list("tool_code", flat=True))
        # 急补整体在前且内部升序，清空后才轮到普通。
        self.assertEqual(order, ["T03", "T05", "T01"])

    def test_acceptance_bing_then_ding(self):
        """验收场景：先交普通丙刀，再交急补丁刀。

        下一笔必须是丁；认领实拿丁；再领才轮到丙。
        """
        bing = submit("丙", is_urgent=False)
        ding = submit("丁", is_urgent=True)
        self.assertGreater(bing.id, 0)
        self.assertGreater(ding.id, bing.id)  # 丁确实交得更晚

        nxt = peek_next_pending()
        self.assertEqual(nxt.tool_code, "丁")
        self.assertTrue(nxt.is_urgent)

        first = claim_next_submission(user=None)
        self.assertEqual(first.status, CLAIMED)
        self.assertEqual(first.submission.tool_code, "丁")

        nxt2 = peek_next_pending()
        self.assertEqual(nxt2.tool_code, "丙")
        second = claim_next_submission(user=None)
        self.assertEqual(second.status, CLAIMED)
        self.assertEqual(second.submission.tool_code, "丙")

        self.assertIsNone(peek_next_pending())


class SingleSortingSourceTests(TransactionTestCase):
    """双车道台预览与实际认领必须同源：页面不得私自另算下一笔。"""

    def test_lanes_next_equals_service_head(self):
        submit("丙", is_urgent=False)
        submit("丁", is_urgent=True)
        urgent, normal = lane_snapshot()
        head = peek_next_pending()
        # 左急补右普通，急补非空时 head 必在左车道队首。
        self.assertEqual(urgent[0].pk, head.pk)
        self.assertEqual(head.tool_code, "丁")
        self.assertEqual([r.tool_code for r in normal], ["丙"])

    def test_lanes_next_moves_to_normal_when_urgent_empty(self):
        submit("T01", is_urgent=False)
        urgent, normal = lane_snapshot()
        self.assertEqual(urgent, [])
        self.assertEqual(normal[0].pk, peek_next_pending().pk)

    def test_claim_takes_exactly_the_previewed_head(self):
        submit("丙", is_urgent=False)
        submit("丁", is_urgent=True)
        head_id = peek_next_pending().id
        result = claim_next_submission(user=None, target_id=head_id)
        self.assertEqual(result.status, CLAIMED)
        self.assertEqual(result.submission.id, head_id)

    def test_claim_non_head_target_is_rejected_stale(self):
        submit("T01", is_urgent=True)
        second = submit("T02", is_urgent=True)
        # 拿着不是排序首位的 id 认领 → 明确失败，单子状态不动。
        result = claim_next_submission(user=None, target_id=second.id)
        self.assertEqual(result.status, STALE)
        second.refresh_from_db()
        self.assertEqual(second.status, OffsetSubmission.Status.PENDING)


class ConcurrentClaimTests(TransactionTestCase):
    def test_two_claimpersons_one_wins_one_fails_explicitly(self):
        head = submit("丁", is_urgent=True)
        submit("丙", is_urgent=False)
        a = make_user("auditor_a", User.Role.AUDITOR)
        b = make_user("auditor_b", User.Role.AUDITOR)

        barrier = threading.Barrier(2)
        outcomes = {}

        def claim(name, user):
            barrier.wait()
            try:
                outcomes[name] = claim_next_submission(user=user, target_id=head.id)
            finally:
                from django.db import connection

                connection.close()

        t1 = threading.Thread(target=claim, args=("A", a))
        t2 = threading.Thread(target=claim, args=("B", b))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        statuses = {k: v.status for k, v in outcomes.items()}
        self.assertIn("A", statuses)
        self.assertIn("B", statuses)
        # 恰好一笔成功……
        winners = [k for k, s in statuses.items() if s == CLAIMED]
        self.assertEqual(len(winners), 1, f"应只允许一笔认领，实际 {statuses}")
        # ……另一笔必须是明确失败（锁竞争 LOCKED 或已被领走 TAKEN），绝不双领。
        loser = "B" if winners[0] == "A" else "A"
        self.assertIn(statuses[loser], (LOCKED, TAKEN))

        head.refresh_from_db()
        self.assertEqual(head.status, OffsetSubmission.Status.PROCESSING)
        self.assertEqual(
            OffsetSubmission.objects.filter(status=OffsetSubmission.Status.PROCESSING)
            .values("id")
            .distinct()
            .count(),
            1,
        )
        winner_user = a if winners[0] == "A" else b
        self.assertEqual(head.claimed_by_id, winner_user.id)


class UrgentLockTests(TransactionTestCase):
    def test_urgent_flag_is_non_editable_and_immutable_via_services(self):
        # editable=False：admin/表单层不可改；系统也不提供任何改勾入口。
        field = OffsetSubmission._meta.get_field("is_urgent")
        self.assertFalse(field.editable)
        row = submit("丁", is_urgent=True)
        claim_next_submission(user=None, target_id=row.id)
        row.refresh_from_db()
        self.assertTrue(row.is_urgent)  # 认领流转不改记号


class APITests(TransactionTestCase):
    def setUp(self):
        from django.test import Client

        self.client = Client()
        self.machinist = make_user("machinist", User.Role.MACHINIST)
        self.auditor = make_user("auditor", User.Role.AUDITOR)

    def test_role_capabilities(self):
        self.assertEqual(self.machinist.can_write, True)
        self.assertEqual(self.machinist.can_claim, False)
        self.assertEqual(self.auditor.can_write, False)
        self.assertEqual(self.auditor.can_claim, True)

    def test_machinist_submits_urgent_flag(self):
        res = self.client.post(
            "/api/submissions",
            data={"tool_code": "丁", "offset_um": 20, "is_urgent": True},
            content_type="application/json",
            **auth_header(self.machinist),
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["is_urgent"])
        self.assertEqual(body["status"], "pending")

    def test_auditor_cannot_submit(self):
        res = self.client.post(
            "/api/submissions",
            data={"tool_code": "丁", "offset_um": 1, "is_urgent": True},
            content_type="application/json",
            **auth_header(self.auditor),
        )
        self.assertEqual(res.status_code, 403)
        self.assertEqual(OffsetSubmission.objects.count(), 0)

    def test_machinist_cannot_claim(self):
        submit("丁", is_urgent=True)
        res = self.client.post(
            "/api/submissions/claim",
            data={"target_id": peek_next_pending().id},
            content_type="application/json",
            **auth_header(self.machinist),
        )
        self.assertEqual(res.status_code, 403)

    def test_lanes_endpoint_and_claim_acceptance_flow(self):
        """丙（普通）先交、丁（急补）后交，走 HTTP 完整验收。"""
        for code, urgent in [("丙", False), ("丁", True)]:
            r = self.client.post(
                "/api/submissions",
                data={"tool_code": code, "offset_um": 5, "is_urgent": urgent},
                content_type="application/json",
                **auth_header(self.machinist),
            )
            self.assertEqual(r.status_code, 200)

        lanes = self.client.get("/api/desk/lanes", **auth_header(self.auditor)).json()
        self.assertEqual(lanes["next"]["tool_code"], "丁")
        self.assertTrue(lanes["next"]["is_urgent"])
        self.assertEqual([r["tool_code"] for r in lanes["urgent"]], ["丁"])
        self.assertEqual([r["tool_code"] for r in lanes["normal"]], ["丙"])
        # 复核员看得到记号；总览/车道台均无勾选接口（前端只读徽标）。

        r1 = self.client.post(
            "/api/submissions/claim",
            data={"target_id": lanes["next"]["id"]},
            content_type="application/json",
            **auth_header(self.auditor),
        )
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.json()["tool_code"], "丁")
        self.assertEqual(r1.json()["claimed_by"], "auditor")

        lanes2 = self.client.get("/api/desk/lanes", **auth_header(self.auditor)).json()
        self.assertEqual(lanes2["next"]["tool_code"], "丙")

        r2 = self.client.post(
            "/api/submissions/claim",
            data={"target_id": lanes2["next"]["id"]},
            content_type="application/json",
            **auth_header(self.auditor),
        )
        self.assertEqual(r2.json()["tool_code"], "丙")

        lanes3 = self.client.get("/api/desk/lanes", **auth_header(self.auditor)).json()
        self.assertIsNone(lanes3["next"])

    def test_concurrent_claim_loser_gets_409(self):
        head = submit("丁", is_urgent=True)
        auditor2 = make_user("auditor2", User.Role.AUDITOR)
        barrier = threading.Barrier(2)
        statuses = {}

        def http_claim(name, user):
            barrier.wait()
            res = self.client.post(
                "/api/submissions/claim",
                data={"target_id": head.id},
                content_type="application/json",
                **auth_header(user),
            )
            statuses[name] = res.status_code

        t1 = threading.Thread(target=http_claim, args=("A", self.auditor))
        t2 = threading.Thread(target=http_claim, args=("B", auditor2))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(sorted(statuses.values()), [200, 409])

    def test_claim_empty_queue_is_explicit_failure(self):
        res = self.client.post(
            "/api/submissions/claim",
            data={},
            content_type="application/json",
            **auth_header(self.auditor),
        )
        self.assertEqual(res.status_code, 404)

    def test_no_urgent_toggle_endpoint_exists(self):
        # 交单后无单资源写接口：PATCH/PUT 必须不被允许，改勾无门。
        row = submit("丁", is_urgent=True)
        for method in ("patch", "put"):
            res = getattr(self.client, method)(
                f"/api/submissions/{row.id}",
                data={"is_urgent": False},
                content_type="application/json",
                **auth_header(self.machinist),
            )
            self.assertEqual(res.status_code, 405)
