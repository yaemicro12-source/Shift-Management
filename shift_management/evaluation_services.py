from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import EvaluationItem, EvaluationScore, StaffEvaluation


def calculate_evaluation_score(scores):
    """重要度を反映した5点満点の総合評価を計算する。"""

    weighted_total = Decimal("0")
    total_weight = Decimal("0")

    for score, weight in scores:
        score = Decimal(str(score))
        weight = Decimal(str(weight))

        if score not in (1, 2, 3, 4, 5):
            raise ValidationError("評価点数は1〜5点で指定してください。")

        if weight not in (1, 2, 3):
            raise ValidationError("重要度は低・普通・高から選んでください。")

        weighted_total += score * weight
        total_weight += weight

    if total_weight == 0:
        raise ValidationError("評価項目が登録されていません。")

    return (weighted_total / total_weight).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP
    )


@transaction.atomic
def submit_evaluation(evaluation, item_scores, comment=""):
    """評価点数を保存して提出する。"""

    evaluation = StaffEvaluation.objects.select_for_update().get(
        pk=evaluation.pk
    )

    if evaluation.submitted_at is not None:
        raise ValidationError("この評価はすでに提出済みです。")

    items = list(
        EvaluationItem.objects.filter(
            Q(area__isnull=True) | Q(area=evaluation.area),
            is_active=True
        ).order_by("id")
    )

    try:
        submitted_scores = {
            int(item_id): int(score)
            for item_id, score in item_scores.items()
        }
    except (TypeError, ValueError):
        raise ValidationError("評価点数が正しくありません。")

    expected_ids = {item.id for item in items}

    if not expected_ids:
        raise ValidationError("評価項目が登録されていません。")

    if set(submitted_scores) != expected_ids:
        raise ValidationError("すべての評価項目を採点してください。")

    total_score = calculate_evaluation_score([
        (submitted_scores[item.id], item.weight)
        for item in items
    ])

    EvaluationScore.objects.filter(
        evaluation=evaluation
    ).delete()

    EvaluationScore.objects.bulk_create([
        EvaluationScore(
            evaluation=evaluation,
            item=item,
            item_name=item.name,
            item_weight=item.weight,
            score=submitted_scores[item.id]
        )
        for item in items
    ])

    evaluation.total_score = total_score
    evaluation.comment = comment.strip()
    evaluation.submitted_at = timezone.now()

    evaluation.save(
        update_fields=[
            "total_score",
            "comment",
            "submitted_at",
            "updated_at"
        ]
    )

    return total_score