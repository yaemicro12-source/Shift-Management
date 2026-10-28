from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MaxValueValidator

# Create your models here.

class Area(models.Model):
    """エリア"""

    name = models.CharField(
        max_length=200,
        verbose_name="エリア名")
    
    code = models.CharField(
        max_length=200,
        verbose_name="エリアコード",
        unique=True)
    def __str__(self):
        return self.name
    

class Store(models.Model):
    """店舗"""

    name = models.CharField(
        max_length=200,
        verbose_name="店舗名")
    
    code = models.CharField(
        max_length=200,
        verbose_name="店舗コード",
        unique=True)
    
    area = models.ForeignKey(
        Area,
        on_delete=models.CASCADE,
        verbose_name = "所属エリア"
    ) # 所属エリア

    opening_time = models.TimeField(
    verbose_name="開店時間"
    )

    closing_time = models.TimeField(
        verbose_name="閉店時間"
    )

    def __str__(self):
        return self.name

class StaffProfile(models.Model):
    """スタッフのプロフィール"""

    ROLE_CHOICES = [
    (1, "従業員"),
    (2, "管理者"),
    (3, "オーナー"),
    ]# スタッフの権限

    SUBMISSION_TYPE_CHOICES = [
        (1,"出勤可能日"),
        (2,"出勤不可日"),
    ]

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        verbose_name = "ユーザー選択"
    )# ユーザー情報。Django Userと一対一の関係
    name = models.CharField(
        max_length=100,
        verbose_name="スタッフ名"
    )
    firstname = models.CharField(
            max_length=100,
            verbose_name="スタッフ下の名前",
            blank=True            
        )
    #ユーザーの名前
    role = models.IntegerField( 
        choices=ROLE_CHOICES,
        verbose_name = "権限区分"
        )# 権限区分。従業員・管理者・オーナー
    joined_on = models.DateField(
        verbose_name = "入社日"
    )# 入社日

    hourly_wage = models.PositiveIntegerField(
        verbose_name = "時給"
    )  # 時給

    submission_type = models.IntegerField(
        choices = SUBMISSION_TYPE_CHOICES,
        verbose_name = "シフトの提出方法"
    )# シフトの提出方法
    minimum_available_days = models.PositiveIntegerField(
        verbose_name = "最低シフト提出日数"
    ) # 最低シフト提出日数
    show_submission_warning = models.BooleanField(
        
        default=True,
        verbose_name = "提出不足時の警告"
    )# 提出不足警告を表示するか

    can_view_all_shifts = models.BooleanField(
        default=False,
        verbose_name = "全体シフトの閲覧権限"
    )# 全体シフトの閲覧権限
    can_view_shortage = models.BooleanField(
        default=False,
        verbose_name = "不足枠の閲覧権限"
    )# 不足枠の閲覧権限

    is_position_holder  = models.BooleanField(
        default=False,
        verbose_name = "役職者かどうか"
    )# 役職者か
    is_trainee = models.BooleanField(
        default=False,
        verbose_name = "研修中かどうか"
    ) # 研修中か
    training_days_per_week = models.PositiveSmallIntegerField(
        default=3,
        validators=[MaxValueValidator(5)],
        verbose_name="研修の週あたり目標日数",
    )
    training_end_date = models.DateField(
        null=True,
        blank=True,
        verbose_name = "研修終了予定日"
    )# 研修終了予定日

    probation_end_date = models.DateField(     
        null=True,
        blank=True,
        verbose_name = "使用期間終了日"
    )# 試用期間終了日

    annual_income_limit = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name = "年間収入上限"    
    )  # 年間収入上限
    annual_max_minutes = models.PositiveIntegerField(      
        null=True,
        blank=True,
        verbose_name = "年間可能時間（分）"    
    )     # 年間勤務可能時間(分)

    weekly_max_minutes = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name = "一週間の勤務上限時間（分）"
    )# 一週間の勤務上限時間(分)
    monthly_max_minutes = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name = "一か月の勤務上限時間（分）"
    ) # 一か月の勤務上限時間(分)
    desired_monthly_minutes = models.PositiveIntegerField(  
        null=True,
        blank=True,
        verbose_name = "本人が希望する月の勤務時間（分）"    
    )# 本人が希望する月の勤務時間(分)
    last_train_departure_time = models.TimeField(
        null=True, 
        blank=True,
        verbose_name = "終電時間"
    )# 終電時間

    shift_priority = models.BooleanField(
        default=False,
        verbose_name = "シフト作成時の配置優先度"
    )# シフト作成時の配置優先度
    note = models.TextField( 
        blank=True,
        verbose_name = "管理者用メモ"
    )# 管理者用メモ
    is_active = models.BooleanField(
        default=True,
        verbose_name = "在籍中か"
    )# 在籍中か
    color = models.CharField(
        max_length=20,
        blank=True,
        verbose_name = "スタッフのシフトカラー"
    )# スタッフのシフトカラー

    def __str__(self):
        return self.user.username

class StaffStoreAssignment(models.Model):
    """所属店舗"""

    staff = models.ForeignKey(
        StaffProfile,
        models.CASCADE,
        verbose_name = "スタッフ"
    )# スタッフ
    store =  models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        verbose_name = "所属店舗"
        )# 所属店舗
    is_primary = models.BooleanField(
        verbose_name = "主所属店舗か",
        default=True
    ) # 主所属店舗か
    starts_on = models.DateField(
        verbose_name = "店舗への所属開始日"
    ) # 店舗への所属開始日
    ends_on = models.DateField(
        null=True,
        blank=True,
        verbose_name = "店舗への所属終了日"
    ) # 店舗への所属終了日


class ManagementScope(models.Model):
    """管理者の管理範囲"""

    manager = models.ForeignKey(
    StaffProfile,
    on_delete=models.CASCADE,
    verbose_name = "管理者の管轄範囲"
    )# 管理者の管理管轄範囲
    area = models.ForeignKey(
        Area,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name = "管理対象エリア"
    ) # 管理対象エリア
    store = models.ForeignKey(
        Store,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name = "管理対象店舗"
    )# 管理対象店舗

"""class StaffAvailabilityRule(models.Model):
    #スタッフの勤務可能条件 時間に余裕があれば作成
    自動でシフト組む時の土日に優先して入れたいスタッフの判断など

    staff = models.ForeignKey(
        StaffProfile,
        on_delete=models.CASCADE
        )# スタッフ
    weekday = models.CharField(
        max_length=100,
         verbose_name='曜日')
    availability_type =  models.OneToOneField(
        StaffProfile,
        on_delete=models.CASCADE
        )# 勤務可能・勤務不可・優先希望など
    start_time = models.TimeField(
        null=True,
        blank=True
    ) # 勤務可能開始時間
    end_time = models.TimeField(       
        null=True,
        blank=True) # 勤務可能終了時間
    effective_from = models.DateField(
        null=True,
        blank=True
    )# 条件の適用開始日
    effective_to =models.DateField( 
        null=True,
        blank=True)# 条件の適用終了日
    note = models.TextField(
        null=True,
        blank=True
    )# 勤務条件に関するメモ
"""

class ShiftTimeSlot(models.Model):
    """シフト時間枠"""

    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        verbose_name = "店舗"
        )# 店舗
    name = models.CharField(
        max_length=200,
        verbose_name='シフト枠名')# シフト枠名。朝番・昼番・夜番など
    start_time = models.TimeField(
        verbose_name = "シフト開始時間"
    ) # シフト開始時間
    end_time = models.TimeField(
        verbose_name = "シフト終了次官"
    )# シフト終了時間
    color = models.CharField(
        max_length = 20,
        blank=True,
        verbose_name = "表示色"
    ) # 表示色
    allow_free_input = models.BooleanField(
        default=False,
        verbose_name = "自由な勤務時間入力を許可するか"
    ) # 自由な勤務時間入力を許可するか
    display_order = models.PositiveIntegerField(
        verbose_name = "表示順"
    )  # 表示順
    is_active = models.BooleanField(
        default=True,
        verbose_name = "利用中のシフト枠か"
    )# 利用中のシフト枠か

    def __str__(self):
        return self.name

class ShiftSubmission(models.Model):
    """シフト提出"""
    STATUS_CHOICES = [
        (1,"未提出"),
        (2,"提出済み"),
        (3,"確認済み")
        ]

    staff = models.ForeignKey(
        StaffProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name = "スタッフ"
    ) # スタッフ
    year = models.PositiveIntegerField(
        verbose_name = "対象年"
    ) # 対象年
    month = models.PositiveIntegerField(
        verbose_name = "対象月"
    )# 対象月
    submission_type = models.IntegerField(
        choices=StaffProfile.SUBMISSION_TYPE_CHOICES,
        verbose_name = "シフト提出方法"
    )
    status = models.IntegerField(
        choices = STATUS_CHOICES,
        default = 1,
        verbose_name = "シフト提出ステータス"
    )# 未提出・提出済み・確認済み
    submitted_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name = "提出日時"
    )# 提出日時
    note = models.TextField(
        blank=True,
        verbose_name = "提出に関するメモ"
    )# 提出全体に関するメモ
    is_latest = models.BooleanField(
        default=True,
        verbose_name = "最新の提出かどうか"
    )  # 最新の提出か

class ShiftPreference(models.Model):
    """スタッフの希望シフト"""

    submission  = models.ForeignKey(
        ShiftSubmission,
        on_delete=models.CASCADE,
        verbose_name = "シフト提出情報"
    ) # シフト提出情報
    target_date = models.DateField(
        verbose_name = "対象日"
    )# 対象日
    is_available = models.BooleanField(
        verbose_name = "出勤可能か"
        )
    shift_time_slot = models.ForeignKey(
        ShiftTimeSlot,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name = "希望するシフト枠"
    ) # 希望するシフト枠
    available_start_time = models.TimeField(
        null=True,
        blank=True,
        verbose_name = "勤務可能開始時間"
    ) # 勤務可能開始時間
    available_end_time = models.TimeField(
        null=True,
        blank=True,
        verbose_name = "勤務可能終了時間"
    ) # 勤務可能終了時間
    is_adjustable = models.BooleanField(
        default=False,
        verbose_name = "休みの変更が可能なスタッフか"
    )# 休みの変更が可能か
    adjustment_priority = models.BooleanField(
        default=False,
        verbose_name = "休みを変更の優先"
    ) # 休みを変更の優先
    note = models.TextField(
        blank=True,
        verbose_name = "日付ごとのメモ"
    )# 日付ごとのメモ

class RequiredStaffingRule(models.Model):
    """必要人数設定"""
    WEEKDAY_CHOICES = [
        (0, "月曜日"),
        (1, "火曜日"),
        (2, "水曜日"),
        (3, "木曜日"),
        (4, "金曜日"),
        (5, "土曜日"),
        (6, "日曜日"),
    ]

    SPECIAL_DAY_CHOICES = [
        (1, "通常日"),
        (2, "祝日"),
        (3, "連休初日"),
        (4, "連休中日"),
        (5, "連休最終日"),
    ]
    store  = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        verbose_name = "店舗"
    )# 店舗
    weekday = models.IntegerField(
        choices=WEEKDAY_CHOICES,
        null=True,
        blank=True,
        verbose_name = "曜日"
    ) # 曜日
    target_date = models.DateField(
        null=True,
        blank=True,
        verbose_name = "特定日"
    )# 特定日
    special_day_type = models.IntegerField(
        choices=SPECIAL_DAY_CHOICES,
        null=True,
        blank=True,
        verbose_name = "特別な日の分類"
    )#  通常日・祝日・連休初日・中日・最終日
    shift_time_slot = models.ForeignKey(
        ShiftTimeSlot,
        on_delete=models.CASCADE,
        verbose_name = "必要人数を設定するシフト枠"
    )# 必要人数を設定するシフト枠
    required_count = models.PositiveIntegerField(
        verbose_name = "必要人数"
    )# 必要人数
    reserve_count = models.PositiveIntegerField(
        default=0,
        verbose_name = "予備人数"
    )# 予備人数

class ShiftSchedule(models.Model):
    """エリア単位のシフト作成情報"""
    STATUS_CHOICES = [
        (1,"下書き"),
        (2,"自動生成済み"),
        (3,"確認済み"),
        (4,"公開済み"),

    ]
    area = models.ForeignKey(
        Area,
        on_delete= models.CASCADE,
        verbose_name = "対象エリア"
    ) # 対象エリア
    year = models.PositiveIntegerField(
        verbose_name = "対象年"
    ) # 対象年
    month = models.PositiveIntegerField(
        verbose_name = "対象月"
    )# 対象月
    status = models.IntegerField(
        choices=STATUS_CHOICES,
        default=1,
        verbose_name = "シフト作成ステータス"
    ) # 下書き・自動生成済み・確認済み・公開済み
    generated_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name = "自動生成日時"
    ) # 自動生成日時
    published_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name = "公開日時"
    )# 公開日時
    created_by = models.ForeignKey(
        StaffProfile,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        verbose_name = "作成した管理者"
    )# 作成した管理者

class ShiftAssignment(models.Model):
    """勤務シフト"""
    STATUS_CHOICES = [
        (1,"下書き"),
        (2,"確定"),
        (3,"取消")
    ]
    schedule = models.ForeignKey(
        ShiftSchedule,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name = "対象エリアのシフト作成"
    )# 対象エリアのシフト作成情報
    staff = models.ForeignKey(
        StaffProfile,
        on_delete= models.SET_NULL,
        null=True,
        blank=True,
        related_name="shift_assignments",
        verbose_name = "勤務するスタッフ"
    )# 勤務するスタッフ
    store = models.ForeignKey(
        Store,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name = "実際に勤務する店舗"
    )# 実際に勤務する店舗
    work_date = models.DateField()# 勤務日
    is_training = models.BooleanField(
        default=False,
        verbose_name="研修シフトか",
    )
    shift_time_slot = models.ForeignKey(
        ShiftTimeSlot,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name = "シフト枠"
    ) # シフト枠
    start_time = models.TimeField(
        blank=True,
        null=True,
        verbose_name = "勤務開始時間"
    )# 勤務開始時間
    end_time = models.TimeField(
        blank=True,
        null=True,
        verbose_name = "勤務終了時間"
    ) # 勤務終了時間
    break_minutes = models.PositiveIntegerField(
        default=0,
        verbose_name = "休憩時間"
    )# 休憩時間
    status = models.IntegerField(
        choices= STATUS_CHOICES,
        verbose_name = "シフトステータス"
    )# 下書き・確定・取消
    task = models.TextField(
        blank=True,
        verbose_name = "当日のタスク"
    )# 当日のタスク
    note = models.TextField(
        blank=True,
        verbose_name = "シフトに関するメモ"
    )# シフトに関するメモ
    created_by = models.ForeignKey(
        StaffProfile,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="created_shift_assignments",
        verbose_name = "作成または編集した管理者"
    ) # 作成または編集した管理者

class ShiftShortage(models.Model):
    """不足枠"""

    schedule = models.ForeignKey(
        ShiftSchedule,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name = "対象エリアのシフト" 
    ) # 対象エリアのシフト
    store = models.ForeignKey(
        Store,
        on_delete=models.SET_NULL,
        null=True,
        blank=True ,
        verbose_name = "不足している店舗"
    )# 不足している店舗
    target_date = models.DateField() # 不足している日付
    shift_time_slot = models.ForeignKey(
        ShiftTimeSlot,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name = "不足しているシフト枠"
    ) # 不足しているシフト枠
    required_count = models.PositiveIntegerField(
        verbose_name = "必要人数"
    )# 必要人数
    assigned_count = models.PositiveIntegerField(
        default=0,
        verbose_name = "現在配置されている人数"
    )# 現在配置されている人数
    note = models.TextField(
        blank=True,
        verbose_name = "不足理由や管理者メモ"
    )# 不足理由や管理者メモ

class ShiftAdjustmentRequest(models.Model):
    """勤務調整依頼"""
    STATUS_CHOICES =[
        (1,"送信済み"),
        (2,"承諾"),
        (3,"辞退"),
        (4,"期限切れ"),
    ]

    shortage = models.ForeignKey(
        ShiftShortage,
        on_delete=models.CASCADE,
        verbose_name = "対象となる不足枠"
    )# 対象となる不足枠
    recipient = models.ForeignKey(
        StaffProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="received_adjustment_requests",
        verbose_name = "依頼先スタッフ"
    )# 依頼先スタッフ
    requested_by = models.ForeignKey(
        StaffProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_adjustment_requests",
        verbose_name = "依頼を送信した管理者"
    )# 依頼を送信した管理者
    message = models.TextField(
        blank=True,
        verbose_name = "勤務調査依頼の本文"
    )# 勤務調整依頼の本文
    status = models.IntegerField(
        choices=STATUS_CHOICES,
        verbose_name = "送信ステータス"
    )# 送信済み・承諾・辞退・期限切れ
    response_note = models.TextField(
        blank=True,
        verbose_name = "スタッフからの回答メモ"
    ) # スタッフからの回答メモ
    requested_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name = "依頼日時"
    )# 依頼日時
    responded_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name = "回答日時"
    )# 回答日時
    response_deadline = models.DateTimeField(
        verbose_name = "回答期限"
    )# 回答期限

class MessageThread(models.Model):
    """メッセージスレッド"""

    subject = models.CharField(
        max_length=500,
        blank=True,
        verbose_name='メッセージ件名')# メッセージ件名
    related_date = models.DateField(
        blank=True,
        null=True,
        verbose_name = "関連する日付"
    )# 関連する日付 
    related_shortage = models.ForeignKey(
        ShiftShortage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,verbose_name = "関連するシフト"
    )# 関連するシフト
    related_assignment = models.ForeignKey(
        ShiftAssignment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name = "不足枠以外のシフトの日付"
    )#不足枠以外のシフトの日付
    created_by = models.ForeignKey(
        StaffProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_message_threads",
        verbose_name = "スレッド作成者"
    )# スレッド作成者
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name = "作成日時"
    )# 作成日時
    recipient = models.ForeignKey(
        StaffProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="received_message_threads",
        verbose_name = "宛先スタッフ"
    )

class Message(models.Model):
    """メッセージ"""

    thread = models.ForeignKey(
        MessageThread,
        on_delete=models.CASCADE,
        verbose_name = "メッセージスレッド"
    )# メッセージスレッド
    sender = models.ForeignKey(
        StaffProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name = "送信者"
    )# 送信者
    body = models.TextField(
        verbose_name = "メッセージ本文"
        )# メッセージ本文
    sent_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name = "送信日時"
        ) # 送信日時
    read_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name = "既読日時") # 既読日時

class EvaluationSetting(models.Model):
        """エリアごとの評価制度設定"""

        area = models.OneToOneField(
            Area,
            on_delete=models.CASCADE,
            related_name="evaluation_setting"
        )
        is_enabled = models.BooleanField(
            default=False,
            verbose_name="評価制度を利用する"
        )
        deadline_day = models.PositiveSmallIntegerField(
            default=15,
            verbose_name="毎月の評価提出期限（日）"
        )

        def __str__(self):
            return f"{self.area.name}の評価設定"


class EvaluationItem(models.Model):
    """共通・エリア独自の評価項目"""

    area = models.ForeignKey(
        Area,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="evaluation_items",
        verbose_name="対象エリア"
    )
    name = models.CharField(
        max_length=100,
        verbose_name="評価項目名"
    )
    description = models.TextField(
        blank=True,
        verbose_name="評価基準の説明"
    )
    weight = models.PositiveSmallIntegerField(
        default=1,
        verbose_name="配点の重み"
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="使用中"
    )

    def __str__(self):
        return self.name

class StaffEvaluation(models.Model):
    """スタッフの月次評価シート"""

    area = models.ForeignKey(
        Area,
        on_delete=models.PROTECT,
        related_name="staff_evaluations",
        verbose_name="評価対象エリア"
    )
    evaluator = models.ForeignKey(
        StaffProfile,
        on_delete=models.PROTECT,
        related_name="evaluations_given",
        verbose_name="評価者"
    )
    staff = models.ForeignKey(
        StaffProfile,
        on_delete=models.PROTECT,
        related_name="evaluations_received",
        verbose_name="評価対象スタッフ"
    )
    year = models.PositiveSmallIntegerField(
        verbose_name="評価対象年"
    )
    month = models.PositiveSmallIntegerField(
        verbose_name="評価対象月"
    )
    total_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="総合評価点"
    )
    comment = models.TextField(
        blank=True,
        verbose_name="評価コメント"
    )
    submitted_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="提出日時"
    )
    created_at = models.DateTimeField(
        auto_now_add=True
    )
    updated_at = models.DateTimeField(
        auto_now=True
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "area", "evaluator", "staff",
                    "year", "month"
                ],
                name="unique_monthly_staff_evaluation"
            )
        ]

    def __str__(self):
        return (
            f"{self.year}年{self.month}月 "
            f"{self.staff} / 評価者:{self.evaluator}"
        )


class EvaluationScore(models.Model):
    """評価シートの項目別点数"""

    evaluation = models.ForeignKey(
        StaffEvaluation,
        on_delete=models.CASCADE,
        related_name="scores",
        verbose_name="評価シート"
    )
    item = models.ForeignKey(
        EvaluationItem,
        on_delete=models.PROTECT,
        verbose_name="評価項目"
    )
    item_name = models.CharField(
        max_length=100,
        verbose_name="評価時点の項目名"
    )
    item_weight = models.PositiveSmallIntegerField(
        verbose_name="評価時点の配点の重み"
    )
    score = models.PositiveSmallIntegerField(
        verbose_name="評価点数"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["evaluation", "item"],
                name="unique_evaluation_item_score"
            )
        ]


class EvaluationAdjustment(models.Model):
    """管理者による最終評価値の補正履歴"""

    staff = models.ForeignKey(
        StaffProfile,
        on_delete=models.PROTECT,
        related_name="evaluation_adjustments",
        verbose_name="評価対象スタッフ"
    )
    adjusted_by = models.ForeignKey(
        StaffProfile,
        on_delete=models.PROTECT,
        related_name="adjustments_made",
        verbose_name="変更した管理者"
    )
    previous_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="変更前の最終評価値"
    )
    new_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        verbose_name="変更後の最終評価値"
    )
    reason = models.TextField(
        verbose_name="変更理由"
    )
    adjusted_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="変更日時"
    )

    def __str__(self):
        return f"{self.staff} : {self.previous_score} → {self.new_score}"
class Notification(models.Model):
    recipient = models.ForeignKey(
        StaffProfile,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    event_key = models.CharField(max_length=180)
    category = models.CharField(max_length=20)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    url = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["recipient", "event_key"],
                name="unique_staff_notification_event",
            )
        ]


class ShiftNotificationPlan(models.Model):
    area = models.ForeignKey(
        Area,
        on_delete=models.CASCADE,
    )
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    submission_deadline = models.DateField(
        verbose_name="希望シフト提出期限"
    )
    creation_date = models.DateField(
        verbose_name="シフト作成予定日"
    )
    publication_date = models.DateField(
        verbose_name="シフト発表予定日"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["area", "year", "month"],
                name="unique_area_notification_plan",
            )
        ]