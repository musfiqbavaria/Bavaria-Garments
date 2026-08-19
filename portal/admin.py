from django.contrib import admin
from .models import *


class ReadOnlyAuditAdmin(admin.ModelAdmin):
    """Audit records are evidence: viewable, never editable.

    AuditLog and FileAccessLog were registered bare, so any user with the Django
    is_staff flag could edit or delete access history - which defeats the audit
    trail the compliance requirement depends on. ApprovalDecisionLog is the
    record of who authorised what, so it is locked the same way.
    """
    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


for m in [OrganizationNode,Department,UserProfile,DashboardPage,Employee,AttendanceEvent,MasterOrder,StockItem,StockMovement,FormDefinition,FormSubmission,Alert,ActionItem,Communication,DocumentRecord,BarcodeAsset,FinanceTransaction,ReportSnapshot]:
    admin.site.register(m)

for m in [AuditLog, FileAccessLog, ApprovalDecisionLog]:
    try: admin.site.register(m, ReadOnlyAuditAdmin)
    except admin.sites.AlreadyRegistered: pass

from .models import ApprovalRequest, StockScan, ValueVariance, DeviceIntegration, AttendanceDailySummary
admin.site.register(ApprovalRequest)
admin.site.register(StockScan)
admin.site.register(ValueVariance)
admin.site.register(DeviceIntegration)
admin.site.register(AttendanceDailySummary)

for m in [MaterialMaster,MaterialLot,MaterialReservation,MaterialMovement]:
    try: admin.site.register(m)
    except admin.sites.AlreadyRegistered: pass

for m in [AssetMachine,AssetMaintenance,AssetDowntime,AssetMovement]:
    try: admin.site.register(m)
    except admin.sites.AlreadyRegistered: pass

for m in [BuyerOpportunity,OpportunityQuotation,OpportunityActivity]:
    try: admin.site.register(m)
    except admin.sites.AlreadyRegistered: pass

for m in [CommunicationThread,CommunicationMessage,CommunicationReadReceipt,CommunicationAttachment,CommunicationNotice,CommunicationConnector]:
    try: admin.site.register(m)
    except admin.sites.AlreadyRegistered: pass

try:
    admin.site.register(ProfitFeasibilityGate)
except admin.sites.AlreadyRegistered:
    pass

try:
    admin.site.register(FreeCapacityOpportunity)
except admin.sites.AlreadyRegistered:
    pass

try:
    admin.site.register(BuyerDeliverySLA)
except admin.sites.AlreadyRegistered:
    pass

try:
    admin.site.register(ProfitBeforeSpendControl)
except admin.sites.AlreadyRegistered:
    pass

for m in [StaffSelfServiceProfile,StaffApplication,StaffDocument,StaffDutySummary,StaffPayrollSummary,StaffScheduleEntry,StaffNotification,StaffAnnouncement,StaffEvent]:
    try: admin.site.register(m)
    except admin.sites.AlreadyRegistered: pass

for m in [HRRecruitment,HRLeaveRequest,HRTrainingRecord,HRPerformanceReview,HRComplaintIncident,HRRecognitionReward,HRInternalMobility,HRPolicyAcknowledgement,HRSurveyResult,HRWorkforcePlan]:
    try: admin.site.register(m)
    except admin.sites.AlreadyRegistered: pass

for m in [AttendanceShift,AttendanceGatePass,AttendanceOvertime,AttendanceNPT,AttendanceHoliday,AttendanceDeviceStatus,AttendanceCCTVFeed,AttendanceManualAdjustment]:
    try: admin.site.register(m)
    except admin.sites.AlreadyRegistered: pass

for m in [CuttingPlan,CuttingFabricIssue,CuttingLay,CuttingBundle,CuttingProductionEntry,CuttingVariance,CuttingAutoReport]:
    try: admin.site.register(m)
    except admin.sites.AlreadyRegistered: pass

for m in [EmbroideryPlan,EmbroideryBundleScan,EmbroideryMaterialIssue,EmbroiderySample,EmbroideryProductionEntry,EmbroideryQC,EmbroideryVariance,EmbroideryAutoReport]:
    try: admin.site.register(m)
    except admin.sites.AlreadyRegistered: pass

for m in [LabelPlan,LabelProof,LabelMaterialIssue,LabelProductionEntry,LabelQC,LabelAllocation,LabelVariance,LabelAutoReport]:
    try: admin.site.register(m)
    except admin.sites.AlreadyRegistered: pass

for m in [QCInspectionPlan,QCBundleScan,QCInspection,QCDefect,QCCAPA,QCReleaseGate,QCAutoReport]:
    try: admin.site.register(m)
    except admin.sites.AlreadyRegistered: pass

for m in [HandIronPlan,HandIronBundleScan,HandIronProductionEntry,HandIronQC,HandIronVariance,HandIronAutoReport]:
    try: admin.site.register(m)
    except admin.sites.AlreadyRegistered: pass

for m in [PolyPlan,PolyStockIssue,PolyBundleScan,PolyPackingEntry,PolyQC,PolyVariance,PolyAutoReport]:
    try: admin.site.register(m)
    except admin.sites.AlreadyRegistered: pass

for m in [FinalQCPlan,FinalQCUnitScan,FinalQCInspection,FinalQCDefect,FinalQCCAPA,FinalQCRelease,FinalQCAutoReport]:
    try: admin.site.register(m)
    except admin.sites.AlreadyRegistered: pass

for m in [FinishingPlan,FinishingScan,FinishingProduction,FinishingQC,FinishingAutoReport]:
    try: admin.site.register(m)
    except admin.sites.AlreadyRegistered: pass

for m in [PackingPlan,PackingScan,PackingCarton,PackingProduction,PackingQC,PackingAutoReport]:
    try: admin.site.register(m)
    except admin.sites.AlreadyRegistered: pass

for m in [ShippingPlan,ShippingCartonScan,ShippingDocument,ShippingCost,ShippingPOD,ShippingAutoReport]:
    try: admin.site.register(m)
    except admin.sites.AlreadyRegistered: pass

for m in [SupplierMaster,SupplierDocument,SupplierRFQ,SupplierPurchaseOrder,SupplierReceipt,SupplierInvoice,SupplierPerformance,SupplierAutoReport]:
    try: admin.site.register(m)
    except admin.sites.AlreadyRegistered: pass

for m in [ProcurementRequest,ProcurementComparison,ProcurementCommitment,ProcurementAutoReport]:
    try: admin.site.register(m)
    except admin.sites.AlreadyRegistered: pass

for m in [PurchaseTransaction,PurchaseAmendment,PurchaseThreeWayMatch,PurchaseReturn,PurchaseAutoReport]:
    try: admin.site.register(m)
    except admin.sites.AlreadyRegistered: pass

for m in [SourcingRequest,SourcingCandidate,SourcingQuotation,SourcingSample,SourcingEvaluation,SourcingHandoff,SourcingAutoReport]:
    try: admin.site.register(m)
    except admin.sites.AlreadyRegistered: pass

try: admin.site.register(CEOAutoReport)
except admin.sites.AlreadyRegistered: pass
