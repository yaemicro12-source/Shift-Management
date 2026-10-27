from datetime import date, time

from django.db import transaction
from django.db.models import Q

from shift_management.models import (
    StaffProfile,
    Store,
    ManagementScope,
    ShiftAssignment,
)

YEAR = 2026
MONTH = 9
DAYS = [3, 8, 15, 22]

START_TIME = time(10, 0)
END_TIME = time(18, 0)

created_count = 0
skipped_count = 0

with transaction.atomic():

    managers = StaffProfile.objects.filter(
        role=2,
        is_active=True,
        managementscope__isnull=False,
    ).distinct()

    for manager in managers:

        scopes = ManagementScope.objects.filter(
            manager=manager
        )

        stores = Store.objects.filter(
            Q(managementscope__in=scopes)
            | Q(area__managementscope__in=scopes)
        ).distinct()

        for store in stores:

            # この店舗に所属する一般スタッフ
            staff_members = StaffProfile.objects.filter(
                role=1,
                is_position_holder=False,
                is_active=True,
                staffstoreassignment__store=store,
                staffstoreassignment__starts_on__lte=date(YEAR, MONTH, 22),
            ).filter(
                Q(staffstoreassignment__ends_on__isnull=True)
                | Q(staffstoreassignment__ends_on__gte=date(YEAR, MONTH, 3))
            ).distinct()[:5]

            if not staff_members.exists():
                print(f"対象スタッフなし: {store.name}")
                continue

            for day in DAYS:

                work_date = date(YEAR, MONTH, day)

                # 管理者と一般スタッフを同じ時間帯に配置
                workers = [manager, *staff_members]

                for worker in workers:

                    # 既存シフトがある場合は変更しない
                    exists = ShiftAssignment.objects.filter(
                        staff=worker,
                        store=store,
                        work_date=work_date,
                    ).exists()

                    if exists:
                        skipped_count += 1
                        continue

                    ShiftAssignment.objects.create(
                        staff=worker,
                        store=store,
                        work_date=work_date,
                        start_time=START_TIME,
                        end_time=END_TIME,
                        status=2,
                        break_minutes=60,
                        note="【テストデータ】スタッフ評価動作確認用",
                        created_by=manager,
                    )

                    created_count += 1

                print(
                    f"{work_date} / {store.name} / "
                    f"管理者: {manager.name} / "
                    f"一般スタッフ: {staff_members.count()}名"
                )

print("----------")
print(f"新規登録: {created_count}件")
print(f"既存のためスキップ: {skipped_count}件")
print("サンプルシフトの登録処理が完了しました。")