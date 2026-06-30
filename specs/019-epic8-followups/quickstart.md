# Quickstart: Epic 8 Follow-ups

## 1. Run Focused Tests

```powershell
.venv\Scripts\python.exe -m pytest `
  tests/services/jdr/test_artifact_edit.py `
  tests/services/jdr/test_artifact_elements_freeform.py `
  tests/services/jdr/test_artifact_text_length.py `
  tests/services/jdr/test_player_artifact_reads.py `
  tests/services/jdr/test_player_listing.py `
  -q --tb=short
```

Expected: all targeted tests pass.

## 2. Run Lint

```powershell
.venv\Scripts\python.exe -m ruff check .
```

Expected: no lint errors.

## 3. Manual Smoke Scenarios

1. Create or reuse a transcribed JDR session with generated artifacts.
2. Start an artifact generation job for the session.
3. Attempt a manual artifact edit before the job finishes.
4. Verify the edit returns `409 artifact-busy` and the artifact content is unchanged.
5. Attempt `PUT /artifacts/elements` with `{"elements":[]}`.
6. Verify the unconfirmed request returns `422`.
7. Repeat with `?confirm_empty=true`.
8. Verify the confirmed request returns `200` and stores an empty elements list.
9. For a non-diarised session, declare a PJ via `/players`.
10. Authenticate as the linked player and verify `/me/sessions` plus shared artifact reads include that session.

## 4. Full Validation If Time Allows

```powershell
.venv\Scripts\python.exe -m pytest -q
```

Expected: full test suite passes. If local runtime exceeds the command window, report the timeout honestly and include targeted results.
