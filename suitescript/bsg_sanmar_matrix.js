/**
 * BSG SanMar matrix-child creator (RESTlet).
 *
 * Creates/updates NetSuite *matrix child* items under existing matrix parents —
 * the one operation neither the CSV Import Assistant (numeric-style parents) nor
 * the REST record API (matrix option fields are read-only) can do reliably.
 *
 * POST body:
 *   { "items": [ {
 *       "externalId": "SANMAR-1959911",
 *       "itemId": "K420-Classic Navy-Small",
 *       "style": "2000",                 // parent matrix item Name/Number
 *       "color": "Classic Navy",          // customlist_bsg_matrix_color value name
 *       "size": "Small",                  // customlist_bsg_matrix_size value name
 *       "displayName": "...", "description": "...", "vendorName": "2000",
 *       "incomeAccount": "4100", "cogsAccount": "5100", "assetAccount": "1200",
 *       "taxSchedule": "Taxable", "subsidiary": "Parent Company : ...",
 *       "department": "Apparel", "class": "Tops : Tees",
 *       "location": "Badger Sporting Goods",
 *       "preferredLocation": "Badger Sporting Goods", "costingMethod": "AVG",
 *       "cost": 3.49, "basePrice": 6.00, "currency": "US Dollar"
 *   }, ... ] }
 *
 * Response: { "results": [ { "externalId", "status": created|updated|error,
 *                            "id", "message" } ] }
 *
 * @NApiVersion 2.1
 * @NScriptType Restlet
 */
define(["N/record", "N/search"], (record, search) => {
  const COLOR_LIST = "customlist_bsg_matrix_color";
  const SIZE_LIST = "customlist_bsg_matrix_size";

  const cache = {};
  const memo = (key, fn) => (key in cache ? cache[key] : (cache[key] = fn()));

  function firstId(type, filters) {
    let id = null;
    search.create({ type, filters, columns: ["internalid"] }).run().each((r) => {
      id = r.id;
      return false;
    });
    return id;
  }

  // Parent matrix item by Name/Number (exact). Works for numeric names too —
  // SuiteScript search has none of the CSV's numeric-reference coercion.
  const resolveParent = (style) =>
    memo("parent:" + style, () => firstId("item", [["name", "is", style]]));

  // ACTIVE list values only. An unfiltered search happily returns a RETIRED
  // duplicate (the colour-consolidation pass inactivates them), and NetSuite
  // then rejects the child with "Invalid Field Value <id> for the following
  // field: matrixoptioncustitem_bsg_color" -- 322 children died that way on
  // 2026-08-08 (run 31240077499). If a colour exists ONLY as a retired value
  // the child now fails with the clear "not in <list>" message instead, which
  // points at the value to reactivate or remap.
  const resolveOption = (listId, name) =>
    memo(listId + ":" + name, () =>
      firstId(listId, [["name", "is", name], "AND", ["isinactive", "is", "F"]]));

  const resolveAccount = (numberOrName) =>
    memo("acct:" + numberOrName, () =>
      firstId("account", [["number", "is", numberOrName]]) ||
      firstId("account", [["name", "is", numberOrName]])
    );

  const resolveByName = (type, name) =>
    memo(type + ":" + name, () => firstId(type, [["name", "is", name]]));

  const resolveTaxSchedule = (name) =>
    memo("tax:" + name, () => firstId("taxschedule", [["name", "is", name]]));

  // Class can be a "Parent : Child" path; match the leaf, disambiguating by parent.
  function resolveClass(path) {
    return memo("class:" + path, () => {
      const parts = path.split(":").map((p) => p.trim());
      const leaf = parts[parts.length - 1];
      const filters = [["name", "is", leaf]];
      if (parts.length > 1) {
        const parentId = firstId("classification", [["name", "is", parts[parts.length - 2]]]);
        if (parentId) filters.push("AND", ["parent", "anyof", parentId]);
      }
      return firstId("classification", filters);
    });
  }

  function setIf(rec, field, value) {
    if (value !== undefined && value !== null && value !== "") rec.setValue(field, value);
  }

  // Apply the non-structural fields shared by create and update.
  function applyFields(rec, it) {
    setIf(rec, "displayname", it.displayName);
    setIf(rec, "salesdescription", it.description);
    setIf(rec, "purchasedescription", it.description);
    setIf(rec, "vendorname", it.vendorName);
    setIf(rec, "upccode", it.upc);
    setIf(rec, "incomeaccount", it.incomeAccount && resolveAccount(it.incomeAccount));
    setIf(rec, "cogsaccount", it.cogsAccount && resolveAccount(it.cogsAccount));
    setIf(rec, "assetaccount", it.assetAccount && resolveAccount(it.assetAccount));
    setIf(rec, "taxschedule", it.taxSchedule && resolveTaxSchedule(it.taxSchedule));
    setIf(rec, "department", it.department && resolveByName("department", it.department));
    setIf(rec, "class", it.class && resolveClass(it.class));
    setIf(rec, "location", it.location && resolveByName("location", it.location));
    setIf(rec, "preferredlocation",
      it.preferredLocation && resolveByName("location", it.preferredLocation));
    setIf(rec, "costingmethod", it.costingMethod);
    setIf(rec, "cost", it.cost);
    // Shipping weight + its unit arrive TOGETHER (unit is the internal id --
    // "1" = lb in this account, verified by ns_weightunit_probe.py); the pair
    // must always agree, so the caller computes both from one source value.
    setIf(rec, "weight", it.weight);
    setIf(rec, "weightunit", it.weightUnitId);
    // Units of measure, as internal ids copied from a live reference item
    // (scripts/item_uom_fix.py's proven approach -- never guessed):
    // Primary Units Type + Stock/Purchase/Sale units.
    setIf(rec, "unitstype", it.unitsTypeId);
    setIf(rec, "stockunit", it.stockUnitId);
    setIf(rec, "purchaseunit", it.purchaseUnitId);
    setIf(rec, "saleunit", it.saleUnitId);
    // Image URL fields, guarded like the price sublist: a custom field that
    // isn't deployed must cost that field, not the whole child. The
    // confusingly-named custitem_sanmar_front_image_url holds the BACK view
    // (the front lives on the atlas Image field); custitem_bsgshop_image_url
    // is the storefront's searchable FRONT-view column.
    try {
      setIf(rec, "custitem_bsgshop_image_url", it.shopImageUrl);
    } catch (e) { /* field not deployed */ }
    try {
      setIf(rec, "custitem_sanmar_front_image_url", it.backImageUrl);
    } catch (e) { /* field not deployed */ }
    // The remaining feed views -- multiple images per item (2026-08-05).
    try {
      setIf(rec, "custitem_sanmar_front_flat_url", it.frontFlatUrl);
    } catch (e) { /* field not deployed */ }
    try {
      setIf(rec, "custitem_sanmar_back_flat_url", it.backFlatUrl);
    } catch (e) { /* field not deployed */ }
    try {
      setIf(rec, "custitem_sanmar_swatch_url", it.swatchUrl);
    } catch (e) { /* field not deployed */ }
    // Preferred Vendor: seed the Vendors sublist with the supplying vendor,
    // marked preferred, only when the sublist is EMPTY -- on updates,
    // vendor_sublist.py owns re-ranking and this must not fight it. Pricing
    // ownership (pricing_ownership.py) reads this flag, so an item created
    // without it has no pricing owner until the sublist job runs.
    if (it.preferredVendorId) {
      try {
        if (rec.getLineCount({ sublistId: "itemvendor" }) <= 0) {
          rec.setSublistValue({ sublistId: "itemvendor", fieldId: "vendor", line: 0, value: it.preferredVendorId });
          rec.setSublistValue({ sublistId: "itemvendor", fieldId: "preferredvendor", line: 0, value: true });
          if (it.vendorName) {
            rec.setSublistValue({ sublistId: "itemvendor", fieldId: "vendorcode", line: 0, value: it.vendorName });
          }
        }
      } catch (e) {
        /* vendors sublist shape varies by config; vendor_sublist.py backfills */
      }
    }
    if (it.basePrice !== undefined && it.basePrice !== null) {
      try {
        rec.setSublistValue({ sublistId: "price1", fieldId: "price_1_", line: 0, value: it.basePrice });
      } catch (e) {
        /* price sublist varies by config; reconcile sets it as a fallback */
      }
    }
  }

  function createChild(it) {
    const parentId = resolveParent(it.style);
    if (!parentId) throw new Error("parent matrix item not found for style '" + it.style + "'");
    const colorId = resolveOption(COLOR_LIST, it.color);
    const sizeId = resolveOption(SIZE_LIST, it.size);
    if (!colorId) throw new Error("color '" + it.color + "' not in " + COLOR_LIST);
    if (!sizeId) throw new Error("size '" + it.size + "' not in " + SIZE_LIST);

    const rec = record.create({ type: record.Type.INVENTORY_ITEM });
    rec.setValue("matrixtype", "CHILD");
    rec.setValue("parent", parentId);
    rec.setValue("itemid", it.itemId);
    rec.setValue("matrixoptioncustitem_bsg_color", colorId);
    rec.setValue("matrixoptioncustitem_bsg_size", sizeId);
    setIf(rec, "externalid", it.externalId);
    applyFields(rec, it);
    return rec.save({ enableSourcing: true, ignoreMandatoryFields: false });
  }

  function findExisting(externalId) {
    return externalId ? firstId("item", [["externalid", "is", externalId]]) : null;
  }

  function post(body) {
    const items = (body && body.items) || [];
    const results = items.map((it) => {
      try {
        const existing = findExisting(it.externalId);
        if (existing) {
          const rec = record.load({ type: record.Type.INVENTORY_ITEM, id: existing });
          applyFields(rec, it);
          return { externalId: it.externalId, status: "updated", id: rec.save() };
        }
        return { externalId: it.externalId, status: "created", id: createChild(it) };
      } catch (e) {
        return { externalId: it.externalId, status: "error", message: e.message || String(e) };
      }
    });
    return { results };
  }

  return { post };
});
