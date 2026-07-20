/**
 * @NApiVersion 2.1
 * @NScriptType Restlet
 * @NModuleScope Public
 *
 * BSG CSV-import trigger.
 *
 * Runs NetSuite's native CSV Import (matrix-item create/update) against a
 * *saved import map*, driven from the nightly catalog automation. SuiteTalk
 * (REST/SOAP) has no "run a saved CSV import" call, so this thin RESTlet is the
 * bridge -- it accepts a CSV, submits the import against a saved map, and
 * reports status; it never builds payloads itself.
 *
 * The caller (GitHub workflow) hands over the CSV one of two ways:
 *   * inline -- ``csv`` (raw text) or ``csvBase64`` -- the RESTlet writes it to
 *     the File Cabinet itself (no separate SOAP upload step), or
 *   * ``fileId`` -- a file already staged in the File Cabinet.
 *
 * POST body:
 *   { "jobs": [ { "mappingId": "custimport_bsg_sanmar_child",
 *                 "csv": "External ID,Item Name/Number,...\n...",
 *                 "folderId": 771,
 *                 "fileName": "sanmar_new_children_2026-07-20.csv",
 *                 "name": "SanMar new children 2026-07-20" }, ... ] }
 *   -> submits each job in order and returns its async task id:
 *   { "results": [ { "mappingId": ..., "fileId": ..., "taskId": "...", "ok": true }, ... ] }
 *
 *   Inline jobs require ``folderId`` (the File Cabinet folder to write into).
 *   Matrix ordering (parent import must finish before its children import) is
 *   the caller's job: submit the parent job, poll GET ?taskId=... until it
 *   reports COMPLETE, then submit the child job. CSV imports run async/queued,
 *   so submitting both at once does not guarantee the parent lands first.
 *
 * GET params:
 *   ?taskId=<id>  -> { "taskId": ..., "status": "COMPLETE" | "PENDING" | ... }
 *   (no params)   -> liveness check
 *
 * Deploy as a RESTlet; the deploying role needs the "Import CSV File" permission
 * (Setup) plus SuiteScript. See docs/CSV_AUTOCREATE.md.
 */
define(['N/task', 'N/file', 'N/encode', 'N/log'], function (task, file, encode, log) {
  'use strict';

  // Turn an inline CSV (raw text, or base64 for transport safety) into a File
  // Cabinet file id.
  function stageFile(job) {
    var contents = job.csv;
    if (!contents && job.csvBase64) {
      contents = encode.convert({
        string: job.csvBase64,
        inputEncoding: encode.Encoding.BASE_64,
        outputEncoding: encode.Encoding.UTF_8
      });
    }
    var csvFile = file.create({
      name: job.fileName || (job.mappingId + '.csv'),
      fileType: file.Type.CSV,
      contents: contents,
      folder: Number(job.folderId),
      encoding: file.Encoding.UTF8
    });
    return csvFile.save();
  }

  function resolveFileId(job) {
    if (job.fileId) {
      return Number(job.fileId);
    }
    if (job.csv || job.csvBase64) {
      if (!job.folderId) {
        throw new Error('inline csv job needs folderId');
      }
      return stageFile(job);
    }
    return null;
  }

  function submitJob(job) {
    var fileId = resolveFileId(job);
    var importTask = task.create({ taskType: task.TaskType.CSV_IMPORT });
    // mappingId accepts the saved import map's script id (custimport_*) or its
    // internal id.
    importTask.mappingId = job.mappingId;
    importTask.importFile = file.load({ id: fileId });
    if (job.name) {
      importTask.name = job.name;
    }
    // Queue behaviour is defined by the saved map (Add / Update / Add or
    // Update); we don't override it here so the map stays the single source of
    // truth for create-vs-update semantics.
    return { taskId: importTask.submit(), fileId: fileId };
  }

  function post(body) {
    var jobs = (body && body.jobs) || [];
    var results = [];
    jobs.forEach(function (job) {
      if (!job || !job.mappingId || (!job.fileId && !job.csv && !job.csvBase64)) {
        results.push({
          ok: false,
          error: 'job needs mappingId and one of fileId | csv | csvBase64',
          job: job
        });
        return;
      }
      try {
        var out = submitJob(job);
        log.audit({
          title: 'bsg_csv_import submitted',
          details: 'map=' + job.mappingId + ' file=' + out.fileId + ' task=' + out.taskId
        });
        results.push({
          mappingId: job.mappingId, fileId: out.fileId, taskId: out.taskId, ok: true
        });
      } catch (e) {
        log.error({
          title: 'bsg_csv_import submit failed',
          details: 'map=' + job.mappingId + ' :: ' + e
        });
        results.push({
          mappingId: job.mappingId, ok: false,
          error: String((e && e.message) || e)
        });
      }
    });
    return { results: results };
  }

  function get(params) {
    if (params && params.taskId) {
      try {
        var status = task.checkStatus({ taskId: params.taskId });
        return { taskId: params.taskId, status: status.status };
      } catch (e) {
        return { taskId: params.taskId, ok: false, error: String((e && e.message) || e) };
      }
    }
    return { ok: true, message: 'bsg_csv_import restlet alive' };
  }

  return { post: post, get: get };
});
