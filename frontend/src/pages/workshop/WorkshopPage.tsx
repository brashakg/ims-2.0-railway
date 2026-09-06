// ============================================================================
// IMS 2.0 - Workshop Page
// ============================================================================
// NO MOCK DATA - All data from API
// Wave 3 file diet: this file holds the composition only. State, loaders and
// handlers live in ./useWorkshopPage; each block lives in its own sibling file.

import { WorkshopJobCardPrint } from '../../components/print/WorkshopJobCardPrint';
import { LabelPreviewModal } from '../../components/labels/LabelPreviewModal';
import { LensFittingFormModal } from '../../components/pos/LensFittingFormModal';
import type { LensFittingFormValue } from '../../components/pos/LensFittingFormModal';
import { useWorkshopPage } from './useWorkshopPage';
import { STATUS_CONFIG } from './shared';
import { WorkshopHeader } from './WorkshopHeader';
import { WorkshopKpiStrip } from './WorkshopKpiStrip';
import { WorkshopBoards } from './WorkshopBoards';
import { WorkshopFilters } from './WorkshopFilters';
import { WorkshopJobList } from './WorkshopJobList';
import { WorkshopJobDetail } from './WorkshopJobDetail';
import { QcChecklistModal } from './QcChecklistModal';
import { ReworkModal } from './ReworkModal';
import { CreateJobModal } from './CreateJobModal';

export function WorkshopPage() {
  const page = useWorkshopPage();
  const {
    user, printJob, setPrintJob, storeInfo, storeEntity, labelSpec, setLabelSpec,
    fittingJob, setFittingJob, fittingSaving, handleFittingSave,
    qcModalJob, setQcModalJob, qcBusy, handleQcSubmit,
  } = page;

  return (
    <div className="inv-body">
      <WorkshopHeader page={page} />
      <WorkshopKpiStrip page={page} />
      <WorkshopBoards page={page} />
      <WorkshopFilters page={page} />
      <WorkshopJobList page={page} />
      <WorkshopJobDetail page={page} />

      {/* Print Job Card Modal */}
      {printJob && storeInfo && (
        <WorkshopJobCardPrint
          job={{
            jobNumber: printJob.jobNumber,
            orderNumber: printJob.orderNumber,
            customerName: printJob.customerName,
            customerPhone: printJob.customerPhone,
            frameBrand: (printJob.frameName || '').split(' ')[0],
            frameModel: (printJob.frameName || '').replace(/^[^ ]+ /, ''),
            frameColor: '',
            lensType: printJob.lensType,
            priority: printJob.priority,
            dueDate: printJob.promisedDate,
            assignedTechnician: printJob.assignedToName || printJob.assignedTo,
            status: STATUS_CONFIG[printJob.status].label,
            createdDate: printJob.createdAt,
          }}
          store={storeInfo}
          entity={storeEntity}
          onClose={() => setPrintJob(null)}
        />
      )}

      {/* Thermal Label Preview + Print modal (QZ silent or HTML fallback) */}
      {labelSpec && (
        <LabelPreviewModal
          spec={labelSpec}
          fallbackJob={{
            store_id: user?.activeStoreId,
            store_name: storeInfo?.storeName,
            store_code: storeInfo?.storeCode,
            store_brand: storeInfo?.brand,
            store_gstin: storeInfo?.gstin,
            store_phone: storeInfo?.phone,
            store_address: [storeInfo?.address, storeInfo?.city, storeInfo?.state, storeInfo?.pincode].filter(Boolean).join(', '),
          }}
          onClose={() => setLabelSpec(null)}
        />
      )}

      {/* Fitting-details modal — the SAME component POS uses post-sale, surfaced
          here so a job whose fitting was never confirmed has an in-app remedy.
          Saving it unblocks /start, which unblocks completion, QC and handover. */}
      {fittingJob && (
        <LensFittingFormModal
          isSaving={fittingSaving}
          onSave={(v: LensFittingFormValue) => handleFittingSave(fittingJob.id, v)}
          onBack={() => setFittingJob(null)}
        />
      )}

      {/* QC checklist modal — posts to /qc-checklist (structured items) -> READY or QC_FAILED */}
      {qcModalJob && (
        <QcChecklistModal
          job={qcModalJob}
          busy={qcBusy}
          onCancel={() => setQcModalJob(null)}
          onSubmit={(passed, notes, checklistItems) =>
            // Pass the PRE-submit status so the handler can tell "just reached
            // the shelf" from "QC'd at the shelf" (toast copy + label reprint).
            handleQcSubmit(qcModalJob.id, passed, notes, checklistItems, qcModalJob.status)
          }
        />
      )}

      <ReworkModal page={page} />

      <CreateJobModal page={page} />
    </div>
  );
}

export default WorkshopPage;
