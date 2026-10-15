from django import forms
from .models import ShiftAssignment,StaffProfile

class ShiftAssignmentForm(forms.ModelForm):
        class Meta:
            model = ShiftAssignment
            fields = [
                "staff",
                "store",
                "work_date",
                "shift_time_slot",
                "start_time",
                "end_time",
            ]

            widgets = {
                "start_time": forms.TimeInput(
                    attrs={"type": "time", "id": "start_time"},
                    format="%H:%M"
                ),
                "end_time": forms.TimeInput(
                    attrs={"type": "time", "id": "end_time"},
                    format="%H:%M"
                ),
                "shift_time_slot": forms.HiddenInput(
                    attrs={"id": "shift_time_slot"}
                ),

                "work_date" :forms.HiddenInput(
                      attrs={"id": "work_date"}
                ),
            }

        def clean(self):
            cleaned_data = super().clean()
            start_time = cleaned_data.get("start_time")
            end_time = cleaned_data.get("end_time")
            if start_time!=None and end_time!=None:
                if start_time>=end_time:
                     self.add_error("start_time","開始時間が不正です")
            return cleaned_data
        
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.fields["staff"].label_from_instance = lambda staff: staff.name
            self.fields["staff"].empty_label = "スタッフ"
            self.fields["store"].empty_label = "店舗"
            self.fields["staff"].required = True
            self.fields["store"].required = True

class StaffProfileForm(forms.ModelForm):

        username = forms.CharField(max_length=150)

        class Meta:
            model = StaffProfile
            fields = [
                
                "name",
                "firstname",
                "joined_on",
                "last_train_departure_time",
                "is_trainee",
                "color",
                "note",
                "role",
                "hourly_wage",
                "submission_type",
                "minimum_available_days",
                "show_submission_warning",
                "can_view_all_shifts",
                "is_position_holder",
                "can_view_shortage",
                "shift_priority",
                "training_end_date",
                "probation_end_date",
                "annual_income_limit",
                "desired_monthly_minutes"
            ]

                 
        show_submission_warning = forms.TypedChoiceField(
            choices=[(True, "有り"), (False, "無し")],
            coerce=lambda value: value == "True",
            widget=forms.RadioSelect
        )