# Vendor Programs & Commercial Terms

Reference sheet for each supplier's negotiated program terms — pricing basis,
payment/freight terms, distribution centers, and rebates — and how each maps
(or does not map) into the item feed this repo maintains.

These are commercial back-office terms, not per-SKU data. Only the parts that
affect what we write onto item records are wired into code; everything else is
here for reference.

> **⚠️ CONFIDENTIAL — SI / Sports Inc. rebate figures.** The tiered/quarterly
> rebate details below are provided under Sports Inc. membership and are marked
> confidential by the suppliers ("Failure to maintain the confidentiality of
> this rebate information could affect your SI membership status."). Do not
> share externally. They are recorded here only because they are back-end
> rebates and must **not** be confused with per-item invoice cost.

---

## Momentec Brands (Augusta Sportswear Brands / Founder Sport Group)

Brands: Augusta Sportswear, Holloway, High Five, Russell Athletic, Pacific
Headwear (ASG) and Badger, Alleson Athletic, C2 Sport (Founder).

| Term | Value |
| --- | --- |
| **Discount basis** | **Half MSRP, less 15%** on all stock and custom styles |
| **Minimum order** | 1 piece (stock) |
| **Payment terms** | Net 120 |
| **Freight** | Free on $150+ (else ~$15 flat) |
| Programs | Booking Order Program; At-Once Program (OrderMyGear 2% rebate; Elite Plus pricing on Badger/Alleson/Prosphere; XXL upcharge waived) |
| Contacts | Michael Ferri; Chris Liberto |

**Rebate (CONFIDENTIAL):** quarterly SI tiered rebate on annual spend —
$0–74,999.99 = 3.5%, $75k–149,999.99 = 4.5%, $150k–299,999.99 = 5.5%,
$300k–399,999.99 = 6.5%, $400k+ = 7.5%. Back-end rebate, not an invoice discount.

**How this maps into the build:** the static feed's `Cost` column is the
standard wholesale (= half MSRP). Our net invoiced cost = wholesale × 0.85
(half MSRP less 15%). `scripts/momentec_backfill.py` applies
`MOMENTEC_INVOICE_DISCOUNT = 0.15` and writes the net to both
`custitem_mtec_cost` and the native Purchase Price. MSRP is written as-is to
`custitem_mtec_msrp` / Base Price. The quarterly rebate is **excluded** from
per-item cost (it is a back-end rebate, not an invoice discount).

---

## S&S Activewear

| Term | Value |
| --- | --- |
| **Discount basis** | **Preferred Price Column** — S&S's best everyday price extended to all Sports Inc. dealers |
| **Minimum order** | None |
| **Payment terms** | Net 60 |
| **Freight** | Free on $150+ ground shipments |
| Distribution centers | Fort Worth, TX · Lockport, IL · McDonough, GA · Olathe, KS · Pompano Beach, FL · Reno, NV · Robbinsville, NJ · West Chester Township, OH |
| Contacts | Kendall Whitley; Rob Van Brocklin (TX/LA/GA/AL/MS/FL — lead); Rob Chalmers (New England, VA, Philadelphia, NJ, NY, MA); Jim Watkins (AR/TN/KY/NC/SC/West); Russ Oehmen (WI/MN/IA/NE/KS/MO/OK/CO — lead Game One); Patty Seta-Tabb (WI/MN/IA/NE/KS/MO/OK/CO — West) |

**Pricing note:** the Preferred Price Column is **not** a fixed % off list. S&S
sets it from a weekly competitive-pricing analysis on ~20 high-volume commodity
styles ("market price"), updated as needed. One-time volume buys can be quoted
at the style level by the sales team.

**Rebate (CONFIDENTIAL):** 3% quarterly rebate on all member volume invoiced
through Sports Inc., paid quarterly from Sports Inc. Additional 6% rebate
(Project MSH Super 13) on these brands: adidas, Under Armour, AllPro, Puma Golf,
Oakley, Team 365, Vineyard Vines, Spyder, Imperial, Devon & Jones, North End,
Swannies Golf. Back-end rebates, not invoice discounts.

**How this maps into the build:** cost = the account-scoped Preferred Price the
S&S API returns for our account — no percentage adjustment in code. The 8 DCs
above correspond to the per-warehouse quantity fields
(`custitem_ss_qty_*`); note S&S caps reported inventory at 500 per location
(see field help / `docs/SS_INVENTORY_CAP_REQUEST.md`). Rebates are back-end and
not reflected in per-item cost.

---

## SanMar

Two SI programs share the same tiered rebate ladder (see below): **SanMar Mill /
Branded Basics** and **SanMar Proprietary / Industry Exclusive**.

| Term | Value |
| --- | --- |
| **Discount basis** | **SI Value Basics Pricing, Case Pricing, or Sale Pricing — whichever is lowest** |
| **Minimum order** | None |
| **Payment terms** | Net 90 |
| **Freight** | Free on $200+ |
| Distribution centers (10) | Seattle, WA · Reno, NV · Dallas, TX (2) · Minneapolis, MN · Robbinsville, NJ · Cincinnati, OH · Jacksonville, FL (2) · Phoenix, AZ · Virginia (opening) |
| Ships to | 99% of continental U.S. within 1–2 days |
| Contacts (by region) | Theresa Miller x4740; Cheryl Isaak x6566; Clistia Major x3720; Gina Biswell x4724; Chris Harris x6557; Donna Barnes x4707; Jennifer Vatshell x7652; Michelle Johns x5464; Dawn Grening x5796; Shelby Arnold x4864 |

**Branded Basics coverage:** Jerzees, Gildan, Hanes, Next Level Apparel, Rabbit
Skins, Bella + Canvas, Comfort Colors, A4, Stanley/Stella, American Apparel,
Champion, Anvil, plus Port & Company PC43/PC45/PC55/PC61/PC150.

**Proprietary/Exclusive coverage:** Port Authority, Port & Company, Sport-Tek,
The North Face, Travis Mathew, District, CornerStone, OGIO, Eddie Bauer, New
Era, Nike Golf, Red Kap, Russell Outdoors, Bulwark, Carhartt, Cotopaxi,
Mercer+Mettle, Wonder Wink, Brooks Brothers, Volunteer Knitwear, TenTree, All
Made, Richardson, and Limited Editions Spacecraft & Tommy Bahama.

**Rebate (CONFIDENTIAL):** quarterly SI tiered rebate on annual spend, issued 4×
per year ~45 days after quarter end. Separate rebate for Custom/Private Label.

*Milled / Branded ladder:* $0–74,999.99 = 1.50%, $75k–124,999.99 = 2.00%,
$125k–174,999.99 = 2.50%, $175k–199,999.99 = 3.50%, $200k+ = 4.50%.

*Proprietary / Industry-Exclusive ladder:* $0–74,999.99 = 3.00%,
$75k–149,999.99 = 5.00%, $150k–249,999.99 = 7.00%, $250k–324,999.99 = 8.00%,
$325k+ = 9.00%.

**How this maps into the build:** cost basis is "whichever is lowest" of SI
Value Basics / Case / Sale price — this is **not** a clean formula derivable
from the dip.txt feed, so cost is **not** auto-adjusted in code today; the feed
prices (piece/case) are written as-is and pricing is reviewed manually. The 10
DCs above correspond to the per-warehouse quantity fields
(`custitem_sanmar_qty_*`); note the inventory file caps each warehouse at 1,500
(see field help). Rebates are back-end and not reflected in per-item cost.
