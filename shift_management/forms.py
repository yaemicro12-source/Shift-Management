from django import forms
from .models import ShiftAssignment

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

