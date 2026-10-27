from datetime import time
from django.db import transaction
from shift_management.models import Area, Store, ShiftTimeSlot

STORES = [
    ("渋谷", "渋谷１", "demo_shibuya1"),
    ("渋谷", "渋谷２", "demo_shibuya2"),
    ("新宿", "歌舞伎町", "demo_kabukicho"),
]

SLOT_NAMES = ["朝番", "昼番", "夜番"]

with transaction.atomic():
    # 登録済みのシフト枠を時間設定の原本として取得
    templates = {}

    for name in SLOT_NAMES:
        slot = ShiftTimeSlot.objects.filter(
            name=name,
            is_active=True
        ).order_by("id").first()

        if slot is None:
            raise ValueError(
                f"有効な「{name}」が見つかりません。"
            )

        templates[name] = slot

    # 店舗とシフト枠を整備
    for area_name, store_name, store_code in STORES:
        area = Area.objects.get(name=area_name)

        store = Store.objects.filter(
            area=area,
            name=store_name
        ).first()

        if store is None:
            store, created = Store.objects.get_or_create(
                code=store_code,
                defaults={
                    "name": store_name,
                    "area": area,
                    "opening_time": time(10, 0),
                    "closing_time": time(23, 0),
                }
            )

            if store.area_id != area.id:
                raise ValueError(
                    f"店舗コードが別エリアで使用されています: {store_code}"
                )

            print(f"店舗を準備: {store.name}")

        for name in SLOT_NAMES:
            existing = ShiftTimeSlot.objects.filter(
                store=store,
                name=name,
                is_active=True
            )

            if existing.count() > 1:
                raise ValueError(
                    f"{store.name} の{name}が重複しています"
                )

            if not existing.exists():
                original = templates[name]

                ShiftTimeSlot.objects.create(
                    store=store,
                    name=name,
                    start_time=original.start_time,
                    end_time=original.end_time,
                    color=original.color,
                    allow_free_input=original.allow_free_input,
                    display_order=original.display_order,
                    is_active=True,
                )

                print(
                    f"シフト枠を追加: {store.name} / {name}"
                )

    # スタッフ属性と前月シフトをまとめて登録
    with open(
        "seed_demo_staff_v3.py",
        encoding="utf-8-sig"
    ) as f:
        exec(compile(f.read(), "seed_demo_staff_v3.py", "exec"))

print("すべての登録処理が終了しました。")
