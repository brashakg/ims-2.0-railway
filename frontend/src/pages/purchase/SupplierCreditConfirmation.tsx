import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { X } from 'lucide-react';
import {
  supplierCreditsApi,
  type ConfirmSupplierCredit,
  type CreditAllocation,
  type CreditBillCandidate,
  type CreditSourceSelection,
  type SupplierCreditPreview,
  type SupplierCreditResult,
} from '../../services/api/supplierCredits';
import { errMsg } from './invoices/shared';

const HEADS = ['taxable', 'cgst', 'sgst', 'igst'] as const;
type Head = typeof HEADS[number];
type AmountInputs = Record<Head, string>;
type Choice = CreditSourceSelection & { key: string };
const choiceKey = (selection: Pick<CreditSourceSelection, 'return_line_index' | 'grn_id' | 'bill_id' | 'bill_line_index'>) =>
  JSON.stringify([selection.return_line_index, selection.grn_id, selection.bill_id, selection.bill_line_index]);
const money = (value: number | null) => value == null ? 'Not yet verified' :
  `₹${value.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const headLabel: Record<Head, string> = { taxable: 'Goods value', cgst: 'CGST', sgst: 'SGST', igst: 'IGST' };
const inputClass = 'w-full rounded border border-gray-300 px-3 py-2 text-sm disabled:bg-gray-100';

// Work in integer paise when comparing the entered note with its allocations.
function paise(text: string): number | null {
  if (!/^\d+(?:\.\d{1,2})?$/.test(text.trim())) return null;
  const value = Math.round(Number(text) * 100);
  return Number.isSafeInteger(value) ? value : null;
}

export interface SupplierCreditConfirmationProps {
  returnId: string;
  canRecord: boolean;
  onClose: () => void;
  onSaved: (result: SupplierCreditResult) => void;
}

/** The existing return's supplier-confirmation action; never issues our own note. */
export function SupplierCreditConfirmation(props: SupplierCreditConfirmationProps) {
  // A different return or revoked permission discards all loaded money and
  // request identity, including a previous return's failed verification.
  return props.canRecord ? <SupplierCreditForm key={props.returnId} {...props} /> : null;
}

function SupplierCreditForm({ returnId, canRecord, onClose, onSaved }: SupplierCreditConfirmationProps) {
  const [preview, setPreview] = useState<SupplierCreditPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [number, setNumber] = useState('');
  const [date, setDate] = useState('');
  const [amount, setAmount] = useState('');
  const [notes, setNotes] = useState('');
  const [amounts, setAmounts] = useState<Record<string, AmountInputs>>({});
  const [choices, setChoices] = useState<Choice[]>([]);
  const [resumeId, setResumeId] = useState('');
  const [uncertain, setUncertain] = useState(false);
  const [verificationFailed, setVerificationFailed] = useState(false);
  const requestVersion = useRef(0);
  const submitBusy = useRef(false);
  const requestId = useRef('');
  const submitted = useRef<ConfirmSupplierCredit | null>(null);
  const invalidateRequests = useCallback(() => { ++requestVersion.current; }, []);

  const applyPreview = useCallback((next: SupplierCreditPreview, resetNote: boolean) => {
    setPreview(next);
    setResumeId(next.confirmation?.confirmation_id ?? '');
    setVerificationFailed(false);
    setAmounts(Object.fromEntries(next.sources.map(source => {
      const saved = next.confirmation?.allocations?.find(row => row.source_key === source.source_key);
      return [source.source_key, Object.fromEntries(HEADS.map(head => [head, String(saved?.[head] ?? source.suggested[head])])) as AmountInputs];
    })));
    setChoices(next.source_selections.map(selection => ({ ...selection, key: choiceKey(selection) })));
    if (resetNote || next.confirmation?.retry_unchanged) {
      const existing = next.confirmation;
      setNumber(existing?.credit_note_number ?? '');
      setDate(existing?.credit_note_date ?? '');
      setAmount(existing ? String(existing.received_amount) : next.remaining_amount == null ? '' : String(next.remaining_amount));
      setNotes(existing?.notes ?? '');
      requestId.current = existing?.confirmation_id ?? crypto.randomUUID();
      submitted.current = existing?.retry_unchanged ? existing.retry_payload ?? null : null;
      setUncertain(!!existing?.retry_unchanged);
    }
  }, []);

  useEffect(() => {
    const version = ++requestVersion.current;
    if (!canRecord) return;
    supplierCreditsApi.preview(returnId).then(next => {
      if (version === requestVersion.current) applyPreview(next, true);
    }).catch(cause => {
      if (version === requestVersion.current) setError(errMsg(cause, 'Could not verify the original bills. Please retry.'));
    }).finally(() => {
      if (version === requestVersion.current) setLoading(false);
    });
    return invalidateRequests;
  }, [returnId, canRecord, applyPreview, invalidateRequests]);

  async function refresh(selection?: CreditSourceSelection[], confirmationId = resumeId || (uncertain ? requestId.current : ''), resetNote = false) {
    const version = ++requestVersion.current;
    setLoading(true);
    setError('');
    try {
      const next = await supplierCreditsApi.preview(returnId, {
        ...(selection ? { source_selections: selection } : {}),
        ...(confirmationId ? { confirmation_id: confirmationId } : {}),
      });
      if (version === requestVersion.current) {
        const identityChanged = next.confirmation?.confirmation_id !== preview?.confirmation?.confirmation_id;
        const recoveryChanged = !!next.confirmation?.retry_unchanged !== !!preview?.confirmation?.retry_unchanged ||
          (uncertain && next.confirmation?.retry_unchanged === false);
        applyPreview(next, resetNote || !preview || identityChanged || recoveryChanged);
      }
    } catch (cause) {
      if (version === requestVersion.current) {
        setVerificationFailed(true);
        setError(errMsg(cause, 'Could not verify the selected original bills.'));
      }
    } finally {
      if (version === requestVersion.current) setLoading(false);
    }
  }

  function selectCandidate(lineIndex: number, candidate: CreditBillCandidate, key: string, checked: boolean) {
    setChoices(current => checked ? [...current.filter(choice => choice.key !== key), {
      key, return_line_index: lineIndex, grn_id: candidate.grn_id, bill_id: candidate.bill_id,
      bill_line_index: candidate.bill_line_index, quantity: candidate.quantity, stock_ids: candidate.stock_ids,
    }] : current.filter(choice => choice.key !== key));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!canRecord || !preview || loading || verificationFailed || submitBusy.current) return;
    setError('');
    let payload = uncertain ? submitted.current : null;
    if (uncertain && !payload) {
      setError('The original request could not be recovered. Close and reopen this note before retrying.');
      return;
    }
    if (!payload) {
      const received = paise(amount);
      if (!number.trim() || !date || received == null || received <= 0) {
        setError('Enter the supplier note number, its date and a positive amount with at most two decimal places.');
        return;
      }
      const allocations: CreditAllocation[] = [];
      let allocated = 0;
      if (preview.source_status === 'RESOLVED') {
        for (const source of preview.sources) {
          const row: CreditAllocation = { source_key: source.source_key, taxable: 0, cgst: 0, sgst: 0, igst: 0 };
          for (const head of HEADS) {
            const value = paise(amounts[source.source_key]?.[head] ?? '');
            if (value == null || value > Math.round(source.remaining_caps[head] * 100)) {
              setError(`Enter valid ${headLabel[head]} within the remaining original-bill amount for ${source.product_name}.`);
              return;
            }
            row[head] = value / 100;
            if (head === 'taxable' || !source.reverse_charge) allocated += value;
          }
          allocations.push(row);
        }
        if (allocated !== received) {
          setError('The credited item amounts must add up to the supplier note amount.');
          return;
        }
      }
      payload = {
        idempotency_key: requestId.current,
        credit_note_number: number.trim(), credit_note_date: date, received_amount: received / 100,
        notes: notes.trim(), source_selections: preview.source_selections, allocations,
      };
      submitted.current = payload;
    }
    submitBusy.current = true;
    setSaving(true);
    const version = requestVersion.current;
    try {
      const result = await supplierCreditsApi.confirm(returnId, payload);
      if (version !== requestVersion.current) return;
      setUncertain(false);
      onSaved(result);
    } catch (cause) {
      if (version !== requestVersion.current) return;
      const status = (cause as { response?: { status?: number } })?.response?.status;
      // A lost reply may already have posted. Keep this exact request until
      // the server confirms its outcome; never make a second note on retry.
      setUncertain(wasUncertain => wasUncertain || !status || status >= 500);
      if (status === 409) setVerificationFailed(true);
      setError(errMsg(cause, 'Could not confirm the result. Retry this same note to check its status.'));
    } finally {
      submitBusy.current = false;
      setSaving(false);
    }
  }

  if (!canRecord) return null;
  const immutable = !!preview?.confirmation || uncertain;
  const locked = loading || saving || uncertain;
  const pending = preview?.source_status === 'PENDING_EVIDENCE';
  const alreadyPosted = preview?.confirmation?.posting_status === 'POSTED';

  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
    <section role="dialog" aria-modal="true" aria-labelledby="supplier-credit-title" className="w-full max-w-4xl max-h-[90vh] overflow-y-auto rounded-lg bg-white p-5">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <h2 id="supplier-credit-title" className="text-lg font-semibold">Confirm supplier credit note</h2>
          <p className="mt-1 text-sm text-gray-600">Record the supplier’s actual note. Only confirmed, matched amounts reduce supplier dues and eligible GST credit.</p>
        </div>
        <button type="button" aria-label="Close supplier credit confirmation" disabled={saving} onClick={onClose} className="rounded p-1 hover:bg-gray-100"><X className="h-5 w-5" /></button>
      </div>
      {error && <p role="alert" className="mb-3 rounded bg-red-50 p-3 text-sm text-red-800">{error}</p>}
      {uncertain && <p className="mb-3 rounded bg-amber-50 p-3 text-sm text-amber-900">The result is not yet confirmed. Retry the same note before entering another one.</p>}
      {loading && <p role="status" className="py-3 text-sm text-gray-600">Checking original bills…</p>}
      {!preview && !loading && <button type="button" className="rounded border px-3 py-2" onClick={() => void refresh()}>Retry bill check</button>}
      {preview && verificationFailed && <button type="button" disabled={loading} className="mb-3 rounded border px-3 py-2 text-sm" onClick={() => void refresh()}>Refresh original bills</button>}
      {preview && <form onSubmit={submit}>
        {preview.confirmations.some(row => row.posting_status === 'PENDING_EVIDENCE') && <label className="mb-4 block text-sm">
          Supplier note to complete
          <select className={inputClass} value={resumeId} disabled={locked} onChange={event => {
            const id = event.target.value;
            setResumeId(id);
            void refresh(undefined, id, true);
          }}>
            <option value="">New supplier note</option>
            {preview.confirmations.filter(row => row.posting_status === 'PENDING_EVIDENCE').map(row =>
              <option key={row.confirmation_id} value={row.confirmation_id}>{row.credit_note_number} · {row.credit_note_date} · {money(row.received_amount)} — pending bill details</option>)}
          </select>
        </label>}
        <div className="grid gap-3 sm:grid-cols-3">
          <label className="text-sm">Supplier note number<input required className={inputClass} value={number} disabled={locked || immutable} onChange={event => setNumber(event.target.value)} /></label>
          <label className="text-sm">Supplier note date<input required type="date" className={inputClass} value={date} disabled={locked || immutable} onChange={event => setDate(event.target.value)} /></label>
          <label className="text-sm">Actual credited amount (₹)<input required inputMode="decimal" className={inputClass} value={amount} disabled={locked || immutable} onChange={event => setAmount(event.target.value)} /></label>
        </div>
        <p className="my-3 text-sm text-gray-600">Already posted: {money(preview.posted_amount)} · Remaining expected credit: {money(preview.remaining_amount)}</p>
        {pending && <div className="mb-4 rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
          <p className="font-medium">Original bill details need verification</p>
          <ul className="mt-1 list-disc pl-5">{preview.pending_reasons.map((reason, index) => <li key={`${reason.code}-${index}`}>{reason.message}</li>)}</ul>
          <p className="mt-2">You can retain the supplier note as pending. Supplier dues and GST will stay unchanged until the complete note is matched.</p>
        </div>}
        {pending && preview.choices.length > 0 && <fieldset disabled={locked} className="mb-4 rounded border p-3">
          <legend className="px-1 text-sm font-medium">Choose the original bills</legend>
          {preview.choices.map(line => <div key={line.return_line_index} className="mb-3">
            <p className="mb-1 text-sm font-medium">{line.product_name} · {line.quantity} pieces returned</p>
            {line.candidates.length === 0 && <p className="text-sm text-gray-600">No verified original bill is available yet.</p>}
            {line.candidates.map(candidate => {
              const key = choiceKey({ ...candidate, return_line_index: line.return_line_index });
              const chosen = choices.find(choice => choice.key === key);
              return <div key={key} className="mb-2 rounded border border-gray-200 p-2">
                <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={!!chosen} disabled={!!candidate.disabled_reason} onChange={event => selectCandidate(line.return_line_index, candidate, key, event.target.checked)} />
                  <span>{candidate.bill_number} · {candidate.bill_date} · {candidate.quantity} pieces{candidate.disabled_reason && <span className="block text-amber-800">{candidate.disabled_reason}</span>}</span>
                </label>
                {chosen && <div className="mt-2 pl-5">
                  <label className="text-sm">Pieces from this bill<input aria-label={`Pieces from ${candidate.bill_number} for ${line.product_name}`} type="number" min="1" step="1" max={line.quantity} className="ml-2 w-20 rounded border p-1" value={chosen.quantity} onChange={event => setChoices(current => current.map(choice => choice.key === key ? { ...choice, quantity: Number(event.target.value) } : choice))} /></label>
                  {candidate.stock_units && candidate.stock_units.length > 0 && <div className="mt-2 flex flex-wrap gap-3">{candidate.stock_units.map(unit => <label key={unit.stock_id} className="text-sm"><input type="checkbox" className="mr-1" checked={chosen.stock_ids?.includes(unit.stock_id) ?? false} onChange={event => setChoices(current => current.map(choice => choice.key !== key ? choice : { ...choice, stock_ids: event.target.checked ? [...(choice.stock_ids ?? []), unit.stock_id] : (choice.stock_ids ?? []).filter(id => id !== unit.stock_id) }))} />{unit.label}</label>)}</div>}
                </div>}
              </div>;
            })}
          </div>)}
          <button type="button" disabled={choices.length === 0} className="rounded border px-3 py-2 text-sm disabled:opacity-50" onClick={() => void refresh(choices.map(choice => ({ return_line_index: choice.return_line_index, grn_id: choice.grn_id, bill_id: choice.bill_id, bill_line_index: choice.bill_line_index, quantity: choice.quantity, stock_ids: choice.stock_ids })))}>Check selected bills</button>
        </fieldset>}
        {!pending && preview.sources.map(source => <fieldset key={source.source_key} disabled={locked || alreadyPosted} className="mb-3 rounded border p-3">
          <legend className="px-1 text-sm font-medium">{source.product_name} · {source.quantity} pieces</legend>
          <p className="mb-2 text-sm text-gray-600">Original bill: {source.bill_number} · {source.bill_date}</p>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">{HEADS.map(head => <label key={head} className="text-sm">{headLabel[head]} (₹)
            <input aria-label={`${headLabel[head]} for ${source.product_name} (${source.bill_number})`} inputMode="decimal" className={inputClass} value={amounts[source.source_key]?.[head] ?? ''} onChange={event => setAmounts(current => ({ ...current, [source.source_key]: { ...current[source.source_key], [head]: event.target.value } }))} />
            <span className="mt-1 block text-xs text-gray-500">Remaining: {money(source.remaining_caps[head])}</span>
          </label>)}</div>
          {!source.claims_credit && <p className="mt-2 text-xs text-gray-600">The original bill did not claim this input tax; this note will not reverse it.</p>}
          {source.reverse_charge && <p className="mt-2 text-xs text-gray-600">Reverse-charge GST is separate from the amount credited by the supplier.</p>}
        </fieldset>)}
        <label className="mb-4 block text-sm">Notes (optional)<textarea className={inputClass} rows={2} value={notes} disabled={locked || alreadyPosted} onChange={event => setNotes(event.target.value)} /></label>
        {alreadyPosted && <p role="status" className="mb-3 rounded bg-green-50 p-3 text-sm text-green-800">This supplier note is already posted.</p>}
        <div className="flex flex-wrap justify-end gap-2 border-t pt-3">
          <button type="button" disabled={saving} className="rounded border px-4 py-2 text-sm" onClick={onClose}>Close</button>
          <button type="submit" disabled={loading || saving || alreadyPosted || verificationFailed} className="rounded bg-bv px-4 py-2 text-sm text-white disabled:opacity-50">{saving ? 'Saving…' : uncertain ? 'Retry same note' : pending ? 'Save supplier note as pending' : 'Confirm supplier credit'}</button>
        </div>
      </form>}
    </section>
  </div>;
}
