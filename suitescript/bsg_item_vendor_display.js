/**
 * @NApiVersion 2.1
 * @NScriptType ClientScript
 * @NModuleScope Public
 *
 * BSG item form vendor-declutter.
 *
 * Hides a vendor's whole field set on the item form when that vendor has no
 * data for the item (its marker field is empty) -- so, e.g., a Gildan 8000
 * that only has S&S + SanMar data doesn't show empty Momentec / UA / Champro
 * fields. Purely cosmetic: it never changes stored values, only display.
 *
 * The per-warehouse quantity fields (custitem_ss_qty_* / custitem_sanmar_qty_*)
 * are included in the S&S / SanMar groups, so once they're on the form they
 * inherit the same show/hide behaviour.
 *
 * Deploy as a Client Script record and attach it to the item custom form(s).
 * See docs/CLIENT_SCRIPT_DEPLOY.md.
 */
define([], function () {
  'use strict';

  // vendor -> marker field (empty == vendor has no data) + every field it owns.
  var VENDORS = [
    {
      label: 'SanMar',
      marker: 'custitem_sanmar_style',
      fields: [
        'custitem_sanmar_unique_key', 'custitem_sanmar_inventory_key',
        'custitem_sanmar_size_index', 'custitem_sanmar_style',
        'custitem_sanmar_mf_color', 'custitem_sanmar_gtin', 'custitem_sanmar_map',
        'custitem_sanmar_msrp', 'custitem_sanmar_case_price',
        'custitem_sanmar_case_size', 'custitem_sanmar_status',
        'custitem_sanmar_qty_available', 'custitem_sanmar_qty_by_whse',
        'custitem_sanmar_front_image_url', 'custitem_sanmar_qty_seattle',
        'custitem_sanmar_qty_cincinnati', 'custitem_sanmar_qty_dallas',
        'custitem_sanmar_qty_reno', 'custitem_sanmar_qty_robbinsville',
        'custitem_sanmar_qty_jacksonville', 'custitem_sanmar_qty_minneapolis',
        'custitem_sanmar_qty_phoenix', 'custitem_sanmar_qty_richmond'
      ]
    },
    {
      label: 'S&S',
      marker: 'custitem_ss_sku',
      fields: [
        'custitem_ss_sku', 'custitem_ss_style_id', 'custitem_ss_style',
        'custitem_ss_color_name', 'custitem_ss_color_code', 'custitem_ss_size_name',
        'custitem_ss_size_order', 'custitem_ss_gtin', 'custitem_ss_brand',
        'custitem_ss_map', 'custitem_ss_msrp', 'custitem_ss_piece_price',
        'custitem_ss_dozen_price', 'custitem_ss_case_price', 'custitem_ss_case_size',
        'custitem_ss_weight', 'custitem_ss_qty_available', 'custitem_ss_qty_by_whse',
        'custitem_ss_is_closeout', 'custitem_ss_is_discontinued',
        'custitem_ss_front_image_url', 'custitem_ss_on_model_image_url',
        'custitem_ss_qty_il', 'custitem_ss_qty_ks', 'custitem_ss_qty_ga',
        'custitem_ss_qty_tx', 'custitem_ss_qty_nv', 'custitem_ss_qty_oh',
        'custitem_ss_qty_pa', 'custitem_ss_qty_cn', 'custitem_ss_qty_fo',
        'custitem_ss_qty_ma', 'custitem_ss_qty_ds', 'custitem_ss_qty_cc'
      ]
    },
    {
      label: 'Momentec',
      marker: 'custitem_mtec_item_sku',
      fields: [
        'custitem_mtec_item_sku', 'custitem_mtec_style', 'custitem_mtec_gtin',
        'custitem_mtec_msrp', 'custitem_mtec_cost', 'custitem_mtec_case_size',
        'custitem_mtec_qty_available', 'custitem_mtec_front_image_url'
      ]
    },
    {
      label: 'Under Armour',
      marker: 'custitem_ua_part_id',
      fields: [
        'custitem_ua_part_id', 'custitem_ua_style', 'custitem_ua_gtin',
        'custitem_ua_qty_available', 'custitem_ua_front_image_url'
      ]
    },
    { label: 'Champro', marker: 'custitem_champro_part_id',
      fields: ['custitem_champro_part_id', 'custitem_champro_style',
        'custitem_champro_gtin', 'custitem_champro_qty_available'] },
    { label: 'United Sports Brands', marker: 'custitem_usb_part_id',
      fields: ['custitem_usb_part_id', 'custitem_usb_style',
        'custitem_usb_gtin', 'custitem_usb_qty_available'] },
    { label: 'Twin City', marker: 'custitem_tck_part_id',
      fields: ['custitem_tck_part_id', 'custitem_tck_style',
        'custitem_tck_gtin', 'custitem_tck_qty_available'] },
    { label: 'Cap America', marker: 'custitem_capamerica_part_id',
      fields: ['custitem_capamerica_part_id', 'custitem_capamerica_style',
        'custitem_capamerica_gtin', 'custitem_capamerica_qty_available'] },
    { label: 'Mizuno', marker: 'custitem_mizuno_part_id',
      fields: ['custitem_mizuno_part_id', 'custitem_mizuno_style',
        'custitem_mizuno_gtin', 'custitem_mizuno_qty_available'] },
    { label: 'Rip-It', marker: 'custitem_ripit_part_id',
      fields: ['custitem_ripit_part_id', 'custitem_ripit_style',
        'custitem_ripit_gtin', 'custitem_ripit_qty_available'] },
    { label: 'Baden', marker: 'custitem_baden_part_id',
      fields: ['custitem_baden_part_id', 'custitem_baden_style',
        'custitem_baden_gtin', 'custitem_baden_qty_available'] }
  ];

  function isEmpty(v) {
    return v === null || v === undefined || String(v).trim() === '';
  }

  function apply(rec) {
    VENDORS.forEach(function (vendor) {
      if (!isEmpty(rec.getValue({ fieldId: vendor.marker }))) {
        return; // vendor has data -> leave its fields visible
      }
      vendor.fields.forEach(function (fid) {
        var fld = rec.getField({ fieldId: fid });
        if (fld) {
          fld.isDisplay = false;
        }
      });
    });
  }

  function pageInit(context) {
    // Only declutter existing records; on create/copy leave everything visible
    // so a manual item can still be filled in.
    if (context.mode === 'create' || context.mode === 'copy') {
      return;
    }
    try {
      apply(context.currentRecord);
    } catch (e) {
      // never let a display tweak block the form from loading
      if (console && console.error) { console.error('bsg_item_vendor_display', e); }
    }
  }

  return { pageInit: pageInit };
});
