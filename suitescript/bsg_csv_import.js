/**
 * @NApiVersion 2.1
 * @NScriptType Restlet
 * @NModuleScope Public
 *
 * BSG CSV-import trigger.
 *
 * Runs NetSuite's native CSV Import (matrix-item create/update) against a
 * *saved import map*, driven from the nightly catalog automation. The external
 * pipeline (GitHub workflow) uploads a fresh create-only CSV to the File
 * Cabinet via SOAP, then calls this RESTlet to kick the import off. SuiteTalk
 * (REST/SOAP) has no "run a saved CSV import" call, so this thin RESTlet is the
 * bridge -- it only submits import jobs and reports their status; it never
 * builds payloads itself.
 *
 * POST body:
 *   { "jobs": [ { "mappingId": "custimport_bsg_sanmar_parent",
 *                 "fileId": 53891,
 *                 "name": "SanMar parents 2026-07-20" }, ... ] }
 *   -> submits each job in order and returns its async task id:
 *   { "results": [ { "mappingId": ..., "fileId": ..., "taskId": "...", "ok": true }, ... ] }
 *
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
 * (Setup) plus SuiteScript. See docs/CSV_IMPORT_AUTOMATION.md.
 */
define(['N/task', 'N/file', 'N/log'], function (task, file, log) {
  'use strict';

  function submitJob(job) {
    var importTask = task.create({ taskType: task.TaskType.CSV_IMPORT });
    // mappingId accepts the saved import map's script id (custimport_*) or its
    // internal id.
    importTask.mappingId = job.mappingId;
    importTask.importFile = file.load({ id: job.fileId });
    if (job.name) {
      importTask.name = job.name;
    }
    // Queue behaviour is defined by the saved map (Add / Update / Add or
    // Update); we don't override it here so the map stays the single source of
    // truth for create-vs-update semantics.
    return importTask.submit();
  }

  function post(body) {
    var jobs = (body && body.jobs) || [];
    var results = [];
    jobs.forEach(function (job) {
      if (!job || !job.mappingId || !job.fileId) {
        results.push({ ok: false, error: 'job needs mappingId and fileId', job: job });
        return;
      }
      try {
        var taskId = submitJob(job);
        log.audit({
          title: 'bsg_csv_import submitted',
          details: 'map=' + job.mappingId + ' file=' + job.fileId + ' task=' + taskId
        });
        results.push({
          mappingId: job.mappingId, fileId: job.fileId, taskId: taskId, ok: true
        });
      } catch (e) {
        log.error({
          title: 'bsg_csv_import submit failed',
          details: 'map=' + job.mappingId + ' file=' + job.fileId + ' :: ' + e
        });
        results.push({
          mappingId: job.mappingId, fileId: job.fileId, ok: false,
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
