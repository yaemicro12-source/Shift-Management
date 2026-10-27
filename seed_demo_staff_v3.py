from collections import defaultdict
from datetime import date, datetime, time, timedelta
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from shift_management.models import Area, Store, StaffProfile, StaffStoreAssignment, ShiftAssignment, ShiftTimeSlot

# このスクリプトが作った勤務シフトのみ、次回実行時に作り直す
MARKER = '[DEMO_SEED_V2]'
SPECS = [
    ('渋谷', '渋谷１', 'demo_shibuya1', 'demo_shibuya1'),
    ('渋谷', '渋谷２', 'demo_shibuya2', 'demo_shibuya2'),
    ('新宿', '歌舞伎町', 'demo_kabukicho', 'demo_kabukicho'),
]
NAMES = [
    '佐藤 葵','鈴木 陽菜','高橋 悠斗','田中 美咲','伊藤 蓮',
    '渡辺 結衣','山本 大翔','中村 咲良','小林 陽翔','加藤 凛',
    '吉田 翔太','山田 美月','佐々木 陽','山口 心春','松本 湊',
    '井上 莉子','木村 蒼','林 ひなた','清水 悠真','斎藤 芽依',
    '池田 陽向','橋本 結菜','阿部 大和','森 七海','石川 陸',
    '前田 花音','藤田 朝陽','岡田 美羽','後藤 樹','長谷川 琴音',
]
SLOT_RULES = [('朝番', 1), ('昼番', 2), ('夜番', 2)]
TODAY = timezone.localdate()
LAST = TODAY.replace(day=1) - timedelta(days=1)
FIRST = LAST.replace(day=1)
DATES = [FIRST + timedelta(days=i) for i in range((LAST - FIRST).days + 1)]

def interval(shift):
    start = shift.start_time or (shift.shift_time_slot.start_time if shift.shift_time_slot_id else None)
    end = shift.end_time or (shift.shift_time_slot.end_time if shift.shift_time_slot_id else None)
    if not start or not end:
        return None
    a = datetime.combine(shift.work_date, start)
    b = datetime.combine(shift.work_date, end)
    if b <= a:
        b += timedelta(days=1)
    return a, b

def overlaps(shift, day, start, end):
    value = interval(shift)
    if value is None:
        return False
    a, b = value
    return a < datetime.combine(day, end) and b > datetime.combine(day, start)

with transaction.atomic():
    for store_index, (area_name, store_name, store_code, prefix) in enumerate(SPECS):
        area = Area.objects.get(name=area_name)
        store = Store.objects.filter(area=area, name=store_name).first()
        if store is None:
            store, _ = Store.objects.get_or_create(
                code=store_code,
                defaults={'name': store_name, 'area': area, 'opening_time': time(10), 'closing_time': time(23)},
            )
            if store.area_id != area.id:
                raise ValueError(f'店舗コードが別エリアで使用されています: {store_code}')

        # 既存スタッフは変更しない。サンプルは10名の固定アカウントで管理する。
        sample_staff = []
        for i in range(10):
            username = f'{prefix}_{i+1:02d}'
            user, created = User.objects.get_or_create(username=username)
            if created:
                user.set_unusable_password()
                user.save(update_fields=['password'])
            profile, created_profile = StaffProfile.objects.get_or_create(
                user=user,
                defaults={
                    'name': NAMES[store_index*10+i],
                    'firstname': NAMES[store_index*10+i].split(' ')[-1],
                    'role': 1,
                    'joined_on': date(2025, 4, 1),
                    'hourly_wage': 1300 + (i % 4)*100,
                    'submission_type': 1 if i % 2 == 0 else 2,
                    'minimum_available_days': 8 + i % 5,
                    'is_position_holder': i == 0,
                    'is_trainee': i in (7, 8),
                    'training_end_date': TODAY + timedelta(days=30) if i in (7, 8) else None,
                    'last_train_departure_time': time(22, 0) if i in (2, 5, 7) else time(23, 30),
                    'weekly_max_minutes': (20 if i in (7, 8) else 40)*60,
                    'monthly_max_minutes': (80 if i in (7, 8) else 160)*60,
                    'desired_monthly_minutes': (60 if i in (7, 8) else 120)*60,
                    'is_active': True,
                    'color': ['#9ec5e8','#e8b9cf','#b9dbc4','#f1d7a8'][i % 4],
                    'note': 'テスト用サンプルスタッフ',
                },
            )
            if not created_profile and profile.note not in ('', 'テスト用サンプルスタッフ'):
                raise ValueError(f'既存ユーザー名と衝突: {username}')
            # 前回のサンプル作成時に不足していた属性を更新する
            profile.is_position_holder = i == 0
            profile.is_trainee = i in (7, 8)
            profile.training_end_date = TODAY + timedelta(days=30) if i in (7, 8) else None
            profile.last_train_departure_time = time(22) if i in (2, 5, 7) else time(23, 30)
            profile.weekly_max_minutes = (20 if i in (7, 8) else 40)*60
            profile.monthly_max_minutes = (80 if i in (7, 8) else 160)*60
            profile.desired_monthly_minutes = (60 if i in (7, 8) else 120)*60
            profile.note = 'テスト用サンプルスタッフ'
            profile.save(update_fields=['is_position_holder','is_trainee','training_end_date','last_train_departure_time','weekly_max_minutes','monthly_max_minutes','desired_monthly_minutes','note'])
            StaffStoreAssignment.objects.get_or_create(
                staff=profile, store=store,
                defaults={'is_primary': True, 'starts_on': date(2025, 4, 1)},
            )
            sample_staff.append(profile)

        # 既存の店舗別シフト枠をそのまま使用。欠落・重複時は変更せず停止。
        slot_map = {}
        for slot_name, _ in SLOT_RULES:
            matches = list(ShiftTimeSlot.objects.filter(
                store=store, name=slot_name, is_active=True
            ))
            if len(matches) != 1:
                raise ValueError(
                    f'{store.name} の「{slot_name}」が有効な枠として1件必要です。'
                    f' 現在 {len(matches)} 件。シフト枠の設定を確認してください。'
                )
            slot_map[slot_name] = matches[0]

        # V2作成分だけ安全に再生成する。以前のスクリプトが作ったものは識別できないため残す。
        ShiftAssignment.objects.filter(store=store, work_date__range=(FIRST, LAST), note=MARKER).delete()
        existing = list(ShiftAssignment.objects.filter(
            store=store, work_date__range=(FIRST, LAST), status=2
        ).select_related('shift_time_slot'))
        new_shifts = []
        hours_by_week = defaultdict(int)
        hours_by_month = defaultdict(int)
        for day in DATES:
            for slot_name, base in SLOT_RULES:
                slot = slot_map[slot_name]
                start, end = slot.start_time, slot.end_time
                required = base + (1 if day.weekday() >= 5 else 0)
                occupied = {s.staff_id for s in existing + new_shifts if s.work_date == day and overlaps(s, day, start, end)}
                shortage = max(0, required - len(occupied))
                minutes = ((end.hour*60+end.minute) - (start.hour*60+start.minute)) % 1440
                week = day - timedelta(days=day.weekday())
                candidates = []
                for index, person in enumerate(sample_staff):
                    if person.id in occupied:
                        continue
                    if end <= start or end > person.last_train_departure_time:
                        continue
                    if any(s.staff_id == person.id and s.work_date == day and overlaps(s, day, start, end) for s in existing + new_shifts):
                        continue
                    if hours_by_week[(person.id, week)] + minutes > person.weekly_max_minutes:
                        continue
                    if hours_by_month[person.id] + minutes > person.monthly_max_minutes:
                        continue
                    # 朝・昼・夜で勤務できる人が偏らないようにしつつ、勤務時間が少ない人を優先
                    candidates.append((hours_by_month[person.id], hours_by_week[(person.id, week)], (index + day.day) % 10, person))
                candidates.sort(key=lambda x: x[:3])
                for _, _, _, person in candidates[:shortage]:
                    shift = ShiftAssignment(
                        staff=person, store=store, work_date=day,
                        shift_time_slot=slot, start_time=start, end_time=end, status=2, note=MARKER,
                    )
                    new_shifts.append(shift)
                    occupied.add(person.id)
                    hours_by_week[(person.id, week)] += minutes
                    hours_by_month[person.id] += minutes
                if len(occupied) < required:
                    print(f'不足: {store.name} {day} {start:%H:%M}-{end:%H:%M} {len(occupied)}/{required}')
        ShiftAssignment.objects.bulk_create(new_shifts)
        print(f'{area.name} / {store.name}: サンプル10名、追加確定シフト{len(new_shifts)}件')

print(f'完了：{FIRST:%Y/%m} のテストデータを登録しました。')
