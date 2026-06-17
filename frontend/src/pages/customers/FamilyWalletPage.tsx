// ============================================================================
// IMS 2.0 - Family/Household Loyalty Wallet (Feature #49)
// ============================================================================
// A household groups up to 7 customers into ONE shared loyalty-points pool.
// Any member can redeem from anywhere (chain-wide). This page lets staff look
// up a customer's household (or create one), manage members (max 7), see the
// pooled balance, and redeem points -> a store-credit voucher.
//
// OTP NOTE: redemption is meant to be OTP-gated to the primary member's
// mobile, but outbound SMS/WhatsApp is currently DISABLED. So the OTP step is
// DEFERRED here: we attempt a no-OTP redeem (which the owner policy
// `loyalty.pool_redeem_requires_otp` may waive); if the backend still requires
// an OTP we surface a clear "OTP delivery is disabled" message rather than a
// dead end. No outbound message is ever sent from this screen.

import { useCallback, useState } from 'react';
import { Loader2, Search, Users, Wallet, UserPlus, X, Gift } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { customerApi } from '../../services/api/customers';
import { familyWalletApi } from '../../services/api/familyWallet';
import type { Household } from '../../services/api/familyWallet';

const MAX_MEMBERS = 7;

interface CustomerLite {
  customer_id: string;
  name?: string;
  mobile?: string;
  phone?: string;
}

function memberLabel(c: CustomerLite | undefined, id: string): string {
  if (!c) return id;
  const phone = c.mobile || c.phone || '';
  return c.name ? `${c.name}${phone ? ` · ${phone}` : ''}` : id;
}

export function FamilyWalletPage() {
  const { user } = useAuth();
  const { success, error: toastError, info } = useToast();
  const roles = user?.roles || [];
  const canManage = roles.some((r) =>
    ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER'].includes(r),
  );

  // Lookup
  const [lookupPhone, setLookupPhone] = useState('');
  const [lookingUp, setLookingUp] = useState(false);
  const [foundCustomer, setFoundCustomer] = useState<CustomerLite | null>(null);

  // Household state
  const [household, setHousehold] = useState<Household | null>(null);
  const [memberDetails, setMemberDetails] = useState<Record<string, CustomerLite>>({});
  const [busy, setBusy] = useState(false);

  // Add-member search
  const [addPhone, setAddPhone] = useState('');

  // Redeem
  const [redeemPoints, setRedeemPoints] = useState('');
  const [redeemMember, setRedeemMember] = useState('');

  const loadMemberDetails = useCallback(async (ids: string[]) => {
    const map: Record<string, CustomerLite> = {};
    await Promise.all(
      ids.map(async (id) => {
        try {
          const c = await customerApi.getCustomer(id);
          if (c) map[id] = c as CustomerLite;
        } catch {
          /* fail-soft: show the raw id */
        }
      }),
    );
    setMemberDetails(map);
  }, []);

  const refreshHousehold = useCallback(
    async (householdId: string) => {
      const hh = await familyWalletApi.getHousehold(householdId);
      setHousehold(hh);
      await loadMemberDetails(hh.member_customer_ids || []);
    },
    [loadMemberDetails],
  );

  const handleLookup = useCallback(async () => {
    const phone = lookupPhone.trim();
    if (!phone) return;
    setLookingUp(true);
    setHousehold(null);
    setFoundCustomer(null);
    try {
      const res = await customerApi.searchByPhone(phone);
      const list: CustomerLite[] = Array.isArray(res) ? res : res?.customers || [];
      const cust = list[0];
      if (!cust) {
        info('No customer found for that mobile number.');
        return;
      }
      setFoundCustomer(cust);
      const hh = await familyWalletApi.getByCustomer(cust.customer_id);
      if (hh) {
        await refreshHousehold(hh.household_id);
      }
    } catch {
      toastError('Lookup failed. Please try again.');
    } finally {
      setLookingUp(false);
    }
  }, [lookupPhone, info, toastError, refreshHousehold]);

  const handleCreate = useCallback(async () => {
    if (!foundCustomer) return;
    setBusy(true);
    try {
      const hh = await familyWalletApi.createHousehold(
        foundCustomer.customer_id,
        user?.activeStoreId || undefined,
      );
      success('Household wallet created.');
      await refreshHousehold(hh.household_id);
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response
        ?.data?.detail;
      toastError(detail || 'Could not create the household.');
    } finally {
      setBusy(false);
    }
  }, [foundCustomer, user?.activeStoreId, success, toastError, refreshHousehold]);

  const handleAddMember = useCallback(async () => {
    if (!household) return;
    const phone = addPhone.trim();
    if (!phone) return;
    setBusy(true);
    try {
      const res = await customerApi.searchByPhone(phone);
      const list: CustomerLite[] = Array.isArray(res) ? res : res?.customers || [];
      const cust = list[0];
      if (!cust) {
        info('No customer found for that mobile number.');
        return;
      }
      await familyWalletApi.addMember(household.household_id, cust.customer_id);
      success(`Added ${cust.name || cust.customer_id} to the household.`);
      setAddPhone('');
      await refreshHousehold(household.household_id);
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response
        ?.data?.detail;
      toastError(detail ? `Could not add member: ${detail}` : 'Could not add member.');
    } finally {
      setBusy(false);
    }
  }, [household, addPhone, info, success, toastError, refreshHousehold]);

  const handleRemoveMember = useCallback(
    async (customerId: string) => {
      if (!household) return;
      setBusy(true);
      try {
        await familyWalletApi.removeMember(household.household_id, customerId);
        success('Member removed.');
        await refreshHousehold(household.household_id);
      } catch (e: unknown) {
        const detail = (e as { response?: { data?: { detail?: string } } })?.response
          ?.data?.detail;
        toastError(detail ? `Could not remove: ${detail}` : 'Could not remove member.');
      } finally {
        setBusy(false);
      }
    },
    [household, success, toastError, refreshHousehold],
  );

  const handleRedeem = useCallback(async () => {
    if (!household) return;
    const points = parseInt(redeemPoints, 10);
    if (!points || points <= 0) {
      toastError('Enter a positive number of points to redeem.');
      return;
    }
    const member = redeemMember || household.primary_customer_id;
    setBusy(true);
    try {
      // OTP is deferred (SMS dark): attempt a no-OTP redeem; the owner policy
      // may waive the gate. If the backend still requires an OTP, surface it.
      const out = await familyWalletApi.redeem(household.household_id, {
        points,
        redeeming_customer_id: member,
      });
      success(
        `Redeemed ${out.points_redeemed} pts -> voucher ${out.voucher.code} (₹${out.voucher.balance}).`,
      );
      setRedeemPoints('');
      await refreshHousehold(household.household_id);
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response
        ?.data?.detail;
      if (detail === 'otp_required') {
        toastError(
          'Redemption needs an OTP, but OTP delivery (SMS/WhatsApp) is currently ' +
            'disabled. Ask an admin to waive the OTP policy to redeem from the counter.',
        );
      } else {
        toastError(detail ? `Redeem failed: ${detail}` : 'Redeem failed.');
      }
    } finally {
      setBusy(false);
    }
  }, [household, redeemPoints, redeemMember, success, toastError, refreshHousehold]);

  const members = household?.member_customer_ids || [];
  const isFull = members.length >= MAX_MEMBERS;

  return (
    <div className="max-w-4xl mx-auto p-6 space-y-6">
      <header className="space-y-1">
        <div className="flex items-center gap-2">
          <Users className="w-6 h-6 text-bv-red-600" />
          <h1 className="text-2xl font-display font-semibold text-gray-900">
            Family Loyalty Wallet
          </h1>
        </div>
        <p className="text-sm text-gray-500">
          A shared loyalty-points pool for a household (up to {MAX_MEMBERS} members).
          Any member can redeem the pooled balance at any store.
        </p>
      </header>

      {/* Lookup */}
      <div className="card p-5 space-y-3">
        <label className="block text-sm font-medium text-gray-700">
          Find a customer by mobile number
        </label>
        <div className="flex gap-2">
          <input
            className="input-field flex-1"
            placeholder="10-digit mobile"
            value={lookupPhone}
            onChange={(e) => setLookupPhone(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleLookup()}
          />
          <button
            className="btn-primary flex items-center gap-2"
            onClick={handleLookup}
            disabled={lookingUp || !lookupPhone.trim()}
          >
            {lookingUp ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Search className="w-4 h-4" />
            )}
            Look up
          </button>
        </div>

        {foundCustomer && !household && (
          <div className="flex items-center justify-between rounded-md bg-gray-50 px-4 py-3">
            <div className="text-sm text-gray-700">
              <span className="font-medium">{foundCustomer.name || foundCustomer.customer_id}</span>
              {(foundCustomer.mobile || foundCustomer.phone) && (
                <span className="text-gray-500"> · {foundCustomer.mobile || foundCustomer.phone}</span>
              )}
              <div className="text-xs text-gray-400 mt-0.5">Not in any household yet.</div>
            </div>
            {canManage && (
              <button className="btn-primary" onClick={handleCreate} disabled={busy}>
                Create household
              </button>
            )}
          </div>
        )}
      </div>

      {/* Household */}
      {household && (
        <>
          {/* Pooled balance */}
          <div className="card p-5">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="rounded-full bg-bv-red-50 p-3">
                  <Wallet className="w-6 h-6 text-bv-red-600" />
                </div>
                <div>
                  <div className="text-xs uppercase tracking-wide text-gray-400">
                    Pooled balance
                  </div>
                  <div className="text-3xl font-semibold text-gray-900">
                    {(household.pool_balance_points ?? 0).toLocaleString('en-IN')}
                    <span className="text-base font-normal text-gray-400"> pts</span>
                  </div>
                </div>
              </div>
              <div className="text-right text-xs text-gray-400">
                <div>Household {household.household_id}</div>
                <div>
                  {members.length}/{MAX_MEMBERS} members
                </div>
              </div>
            </div>
          </div>

          {/* Members */}
          <div className="card p-5 space-y-3">
            <h2 className="text-sm font-semibold text-gray-700">Members</h2>
            <ul className="divide-y divide-gray-100">
              {members.map((id) => {
                const isPrimary = id === household.primary_customer_id;
                return (
                  <li key={id} className="flex items-center justify-between py-2.5">
                    <span className="text-sm text-gray-800">
                      {memberLabel(memberDetails[id], id)}
                      {isPrimary && (
                        <span className="ml-2 rounded bg-amber-50 px-1.5 py-0.5 text-xs text-amber-700">
                          Primary
                        </span>
                      )}
                    </span>
                    {canManage && !isPrimary && (
                      <button
                        className="text-gray-400 hover:text-bv-red-600"
                        title="Remove member"
                        onClick={() => handleRemoveMember(id)}
                        disabled={busy}
                      >
                        <X className="w-4 h-4" />
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>

            {canManage && (
              <div className="flex gap-2 pt-2">
                <input
                  className="input-field flex-1"
                  placeholder={isFull ? 'Household is full (max 7)' : 'Add member by mobile'}
                  value={addPhone}
                  onChange={(e) => setAddPhone(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleAddMember()}
                  disabled={isFull || busy}
                />
                <button
                  className="btn-secondary flex items-center gap-2"
                  onClick={handleAddMember}
                  disabled={isFull || busy || !addPhone.trim()}
                >
                  <UserPlus className="w-4 h-4" />
                  Add
                </button>
              </div>
            )}
          </div>

          {/* Redeem */}
          <div className="card p-5 space-y-3">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-gray-700">
              <Gift className="w-4 h-4" /> Redeem from pool
            </h2>
            <p className="text-xs text-gray-400">
              Redeeming mints a store-credit voucher the member can spend. OTP delivery
              (SMS/WhatsApp) is currently disabled, so this relies on the store OTP-waive
              policy. No message is sent.
            </p>
            <div className="flex flex-wrap gap-2">
              <select
                className="input-field"
                value={redeemMember}
                onChange={(e) => setRedeemMember(e.target.value)}
              >
                <option value="">Redeeming member (default: primary)</option>
                {members.map((id) => (
                  <option key={id} value={id}>
                    {memberLabel(memberDetails[id], id)}
                  </option>
                ))}
              </select>
              <input
                className="input-field w-40"
                type="number"
                min={1}
                placeholder="Points"
                value={redeemPoints}
                onChange={(e) => setRedeemPoints(e.target.value)}
              />
              <button
                className="btn-primary"
                onClick={handleRedeem}
                disabled={busy || !redeemPoints}
              >
                Redeem
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

export default FamilyWalletPage;
