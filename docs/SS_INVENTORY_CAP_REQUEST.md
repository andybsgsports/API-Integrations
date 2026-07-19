# S&S Activewear — request to lift the API inventory cap

Draft email to send to **api@ssactivewear.com** (put your S&S account number in
the subject line so they can route it quickly). Fill in the bracketed values.

---

**To:** api@ssactivewear.com
**Subject:** Account [YOUR S&S ACCOUNT #] — request to enable full inventory quantities via API

Hello S&S API team,

We integrate your API into our NetSuite catalog and pull per-warehouse
inventory using both the REST `/Inventory` endpoint and the PromoStandards
`GetInventoryLevels` (2.0.0) service.

We're seeing every warehouse's on-hand quantity **capped at 500** in the API
response for our account. For example, a SKU your website shows as
">50,000" at a warehouse comes back as exactly `500` in the API, while
warehouses with fewer than 500 units report their true number. This happens
on both the REST and PromoStandards responses, so it appears to be an
account-level setting rather than anything on our side.

Could you please **enable full/uncapped inventory quantities** on our API
account (account **[YOUR S&S ACCOUNT #]**), so the API returns actual on-hand
counts above 500? If that requires a different entitlement or account tier,
please let us know what's needed.

For reference, a couple of SKUs where we see the 500 cap:
- `B06560504` (Gildan 8000, Black, Medium) — every warehouse reports 500
- `B00760037` (Gildan 2000, Antique Cherry Red, 2XL) — IL/Lockport reports 500

Thank you,
[YOUR NAME]
[COMPANY]
[PHONE / EMAIL]

---

## Background (for our own records — not part of the email)

- Verified on 2026-07-19 against both S&S APIs. Values **below** 500 come
  through exact (e.g. Olathe 252, Reading 273, a warehouse at 498); values
  **at or above** 500 flatten to exactly 500.
- The NetSuite per-warehouse fields mirror the API faithfully — the cap is on
  S&S's side, not in our integration.
- The S&S feed now logs a `WARNING: S&S inventory cap hit …` whenever it sees
  the pattern, and the Field Help on the per-warehouse quantity fields notes
  that a value of 500 can mean "500 or more." Both self-resolve once S&S lifts
  the cap.
- No code change is needed on our end when the cap is lifted; the next feed run
  will pull the real numbers automatically.
