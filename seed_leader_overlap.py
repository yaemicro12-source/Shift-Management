from datetime import datetime, timedelta
from collections import defaultdict
from django.db import transaction
from shift_management.models import (
    Store,
    StaffProfile,
    ShiftAssignment,
)

YEAR = 2026
MONTH = 9

TARGETS = [
    ("渋谷１", "佐藤 葵"),
    ("渋谷２", "吉田 翔太"),
]

def interval(shift):
    start = shift.start_time
    end = shift.end_time

    if start is None and shift.shift_time_slot:
        start = shift.shift_time_slot.start_time

    if end is None and shift.shift_time_slot:
        end = shift.shift_time_slot.end_time

    if start is None or end is None:
        return None

    a = datetime.combine(shift.work_date, start)
    b = datetime.combine(shift.work_date, end)

    if b <= a:
        b += timedelta(days=1)

    return a, b

def overlaps(a, b):
    if a is None or b is None:
        return False

    return (
        min(a[1], b[1]) - max(a[0], b[0])
    ).total_seconds() >= 60

with transaction.atomic():

    for store_name, leader_name in TARGETS:

        store = Store.objects.get(name=store_name)

        leader = StaffProfile.objects.get(
            name=leader_name,
            is_position_holder=True,
            is_active=True,
        )

        shifts = list(
            ShiftAssignment.objects.filter(
                store=store,
                work_date__year=YEAR,
                work_date__month=MONTH,
                status=2,
            ).select_related(
                "staff",
                "shift_time_slot",
            )
        )

        general = [
            s for s in shifts
            if s.staff_id
            and s.staff.role == 1
            and not s.staff.is_position_holder
            and s.staff.is_active
            and interval(s) is not None
        ]

        leaders = [
            s for s in shifts
            if s.staff_id == leader.id
        ]

        all_ids = {s.staff_id for s in general}

        covered = set()

        for ls in leaders:
            for gs in general:
                if (
                    ls.work_date == gs.work_date
                    and overlaps(interval(ls), interval(gs))
                ):
                    covered.add(gs.staff_id)

        created = 0

        while covered != all_ids:

            best = None
            best_new = set()

            dates_with_leader = {
                s.work_date for s in leaders
            }

            for candidate in general:

                # 役職者の既存勤務日には追加しない
                if candidate.work_date in dates_with_leader:
                    continue

                ci = interval(candidate)

                if ci is None:
                    continue

                # 同じ日・同じ時間帯のスタッフをまとめて対象にする
                matching = {
                    s.staff_id
                    for s in general
                    if s.work_date == candidate.work_date
                    and overlaps(ci, interval(s))
                }

                newly_covered = matching - covered

                if len(newly_covered) > len(best_new):
                    best = candidate
                    best_new = newly_covered

            if best is None or not best_new:
                break

            # 既存のシフト枠・勤務時間をそのまま使用
            new_shift = ShiftAssignment.objects.create(
                staff=leader,
                store=store,
                work_date=best.work_date,
                shift_time_slot=best.shift_time_slot,
                start_time=best.start_time,
                end_time=best.end_time,
                break_minutes=0,
                status=2,
                note="評価機能テスト用・役職者勤務",
            )

            leaders.append(new_shift)
            created += 1

            # 実際に勤務時間が重なったスタッフを反映
            for gs in general:
                if (
                    gs.work_date == new_shift.work_date
                    and overlaps(interval(new_shift), interval(gs))
                ):
                    covered.add(gs.staff_id)

        print()
        print(f"【{store_name} / {leader_name}】")
        print(f"追加した役職者シフト: {created}件")
        print(f"勤務が重なるスタッフ: {len(covered)}/{len(all_ids)}人")

        if covered != all_ids:
            missing = StaffProfile.objects.filter(
                id__in=(all_ids - covered)
            ).values_list("name", flat=True)

            print("未カバー:", ", ".join(missing))

print()
print("渋谷エリアの役職者シフト調整が完了しました。")
