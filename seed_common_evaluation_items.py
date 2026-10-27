from shift_management.models import EvaluationItem

items = [
    ("接客", "会社の定める接客レベルに達しているか", 3),
    ("業務内容", "会社の定める業務内容を遂行できているか", 3),
    ("売上", "会社の利益のために動くことができているか", 3),
    ("理念", "会社の理念を理解し行動できているか", 2),
    ("衛生", "職場環境を衛生的に保てているか", 2),
]

for name, description, weight in items:
    item, created = EvaluationItem.objects.update_or_create(
        area=None,
        name=name,
        defaults={
            "description": description,
            "weight": weight,
            "is_active": True,
        },
    )
    print(f"{'Created' if created else 'Updated'}: {item.name}")

print("Common evaluation items:", EvaluationItem.objects.filter(area__isnull=True, is_active=True).count())
