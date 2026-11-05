# Ann ↔ Toast (Johnny Tamales Missouri City)

Ann does **not** replace Toast Loyalty. It adds **referral attribution** (which marketer sent the guest) and a simple points ledger you control, matched to guests by **phone**.

## Today (no Toast API keys needed)

1. In Toast Web: **Customers** (or Reports) → export customers to CSV.  
2. Ann Admin → **Import Toast CSV**.  
3. Columns Ann recognizes (any of these names):  
   - Phone / Mobile Phone / Phone Number / Customer Phone  
   - Name / Customer Name / Guest Name  
   - Email / Email Address  
   - GUID / Customer GUID / Toast GUID / Customer ID  
4. At the table: after Toast closes the check, open Ann **Staff**, enter guest phone, optional Toast order # → check in.

Phone match is 10-digit US (leading `1` stripped).

## Staff flow with Toast

1. Guest pays in Toast as usual.  
2. Ask for phone → Ann Staff check-in.  
3. Points land in Ann; referral stays on the guest record for marketer leaderboard.

## Later (API)

`POST /api/toast/webhook` is a **stub** (returns 501). When JT has Toast Partner / webhook access:

- Map order `customer.phone` → Ann check-in  
- Store Toast customer GUID on `guests.toast_guid`  
- Optionally push Ann points into Toast Loyalty as a custom integration  

Until then, CSV + staff phone check-in is the supported path.

## Do not

- Put non-JT brand names in Ann UI or exports.  
- Email guest lists without AJ’s OK.
