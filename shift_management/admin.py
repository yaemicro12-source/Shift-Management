from django.contrib import admin
from .models import (
    Area,
    Store,
    StaffProfile,
    StaffStoreAssignment,
    ManagementScope,
    ShiftTimeSlot,
    ShiftSubmission,
    ShiftPreference,
    RequiredStaffingRule,
    ShiftSchedule,
    ShiftAssignment,
    ShiftShortage,
    ShiftAdjustmentRequest,
    MessageThread,
    Message,
)

# Register your models here.
admin.site.register(Area)
admin.site.register(Store)
admin.site.register(StaffProfile)
admin.site.register(StaffStoreAssignment)
admin.site.register(ManagementScope)
admin.site.register(ShiftTimeSlot)
admin.site.register(ShiftSubmission)
admin.site.register(ShiftPreference)
admin.site.register(RequiredStaffingRule)
admin.site.register(ShiftSchedule)
admin.site.register(ShiftAssignment)
admin.site.register(ShiftShortage)
admin.site.register(ShiftAdjustmentRequest)
admin.site.register(MessageThread)
admin.site.register(Message)