"""后台认领 / 复核进程。

两种模式都只准调用 desk.services 里的统一排序与认领函数，不得自行排序：

* WORKER_AUTO_CLAIM=1（代码默认）：自己认领下一笔候审单并出结论；
* WORKER_AUTO_CLAIM=0（docker-compose 默认）：只推进复核员已认进
  「复核中」的单子出结论，候审单留给双车道台人工认领。
"""

import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import django


def setup_django() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    django.setup()


def tick(auto_claim: bool) -> bool:
    from desk.services import (
        CLAIMED,
        apply_verdict,
        claim_next_submission,
        finish_next_processing,
    )

    if auto_claim:
        # 与双车道台预览、复核员认领共用同一个排序/认领函数。
        result = claim_next_submission(user=None)
        if result.status != CLAIMED:
            return False
        apply_verdict(result.submission)
        return True

    # 只收口：把复核员认领进「复核中」的单子复核出结论。
    return finish_next_processing()


def run_loop(poll_seconds: float | None = None, auto_claim: bool | None = None) -> None:
    setup_django()
    if poll_seconds is None:
        poll_seconds = float(os.environ.get("WORKER_POLL_SECONDS", "0.5"))
    if auto_claim is None:
        auto_claim = os.environ.get("WORKER_AUTO_CLAIM", "1") == "1"
    mode = "认领+复核" if auto_claim else "仅收口复核中"
    print(f"cnc-offset worker started ({mode}, poll={poll_seconds}s)", flush=True)
    while True:
        if not tick(auto_claim):
            time.sleep(poll_seconds)


if __name__ == "__main__":
    setup_django()
    _auto_claim = os.environ.get("WORKER_AUTO_CLAIM", "1") == "1"
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        tick(_auto_claim)
    else:
        run_loop(auto_claim=_auto_claim)
