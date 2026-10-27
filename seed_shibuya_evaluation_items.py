from shift_management.models import Area, EvaluationItem

area = Area.objects.get(pk=1)

items = [
    ("お客様に積極的に話しかけているか", 1),
    ("お客様に適切な気遣いができているか", 2),
    ("他のスタッフとのコミュニケーションがよくとれているか", 1),
]

for name, weight in items:
    item, created = EvaluationItem.objects.update_or_create(
        area=area,
        name=name,
        defaults={
            "description": "",
            "weight": weight,
            "is_active": True,
        },
    )
    print(f"{'Created' if created else 'Updated'}: {item.name}")

print("Area evaluation items:", EvaluationItem.objects.filter(
    area=area,
    is_active=True
).count())
