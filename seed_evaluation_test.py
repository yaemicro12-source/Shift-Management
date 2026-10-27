from datetime import date, time
from django.db import transaction
from shift_management.models import StaffProfile, Store, ShiftAssignment

store = Store.objects.get(pk=1)
manager = StaffProfile.objects.get(pk=1)
staff_members = list(StaffProfile.objects.filter(pk__in=[2, 3]))

dates = [
    date(2026, 9, 3),
    date(2026, 9, 8),
    date(2026, 9, 15),
    date(2026, 9, 22),
]

created = 0
skipped = 0

with transaction.atomic():
    for work_date in dates:
        for staff in [manager, *staff_members]:
            if ShiftAssignment.objects.filter(
                staff=staff,
                store=store,
                work_date=work_date
            ).exists():
                skipped += 1
                continue

            ShiftAssignment.objects.create(
                staff=staff,
                store=store,
                work_date=work_date,
                start_time=time(10, 0),
                end_time=time(18, 0),
                break_minutes=60,
                status=2,
                created_by=manager,
                note="2026-09 evaluation test"
            )
            created += 1

print(f"Created: {created}")
print(f"Skipped: {skipped}")
