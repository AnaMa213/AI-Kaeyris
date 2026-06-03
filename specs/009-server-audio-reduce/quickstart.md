# Quickstart: Server Audio Reduce

This quickstart validates the planned BD-9 behavior once implementation tasks are complete.

## Preconditions

- Docker image includes `ffmpeg` and `ffprobe`.
- `KAEYRIS_DATA_DIR` points to a writable directory.
- Redis worker is running.
- A GM user/session/campaign exists.
- `KAEYRIS_AUDIO_MAX_UPLOAD_BYTES` is documented. Default: `524288000` bytes.

## Local Verification Flow

1. Start the stack:

   ```powershell
   docker compose up --build
   ```

2. Create or reuse a JDR session.

3. Upload a raw M4A below the configured limit:

   ```powershell
   curl.exe -i `
     -H "Authorization: Bearer $env:KAEYRIS_TOKEN" `
     -F "audio=@D:\path\to\session.m4a;type=audio/mp4" `
     "http://localhost:8000/services/jdr/sessions/$env:SESSION_ID/audio"
   ```

   Expected:

   - HTTP `202`.
   - Response contains `job_id`.
   - Session detail exposes the same `current_job_id`.
   - Job kind remains `transcription`.

4. Poll the job:

   ```powershell
   curl.exe -s `
     -H "Authorization: Bearer $env:KAEYRIS_TOKEN" `
     "http://localhost:8000/services/jdr/jobs/$env:JOB_ID"
   ```

   Expected:

   - Job transitions through the existing statuses.
   - Preparation failures appear as failed processing, not as a new visible job type.

5. After success, verify audio retrieval:

   ```powershell
   curl.exe -I `
     -H "Authorization: Bearer $env:KAEYRIS_TOKEN" `
     "http://localhost:8000/services/jdr/sessions/$env:SESSION_ID/audio"
   ```

   Expected:

   - Audio is still available for playback.
   - Served audio is the retained prepared artifact.

6. Verify raw cleanup on disk:

   ```powershell
   Get-ChildItem -Recurse $env:KAEYRIS_DATA_DIR | Where-Object Name -Match $env:SESSION_ID
   ```

   Expected:

   - No raw temporary upload remains after successful preparation.
   - One retained prepared audio artifact remains until explicit delete.

7. Upload an oversized file or configure a tiny limit for test:

   ```powershell
   $env:KAEYRIS_AUDIO_MAX_UPLOAD_BYTES = "1024"
   ```

   Expected:

   - Upload returns `413`.
  - Error body is Problem Details `audio-upload-too-large` and includes `limit_bytes`.
   - No orphan raw file remains.

8. Delete session audio:

   ```powershell
   curl.exe -i -X DELETE `
     -H "Authorization: Bearer $env:KAEYRIS_TOKEN" `
     "http://localhost:8000/services/jdr/sessions/$env:SESSION_ID/audio"
   ```

   Expected:

   - HTTP `204`.
   - Session returns to `created`.
   - `current_job_id` is null.
   - Raw leftovers, prepared audio, transcription, chunks, and artifacts are removed.

## Automated Checks

Run:

```powershell
ruff check .
pytest
```

Focused tests expected from implementation:

- upload below/above configured limit.
- single visible transcription job after upload.
- worker preparation success updates canonical audio and deletes raw.
- preparation failure marks failed processing.
- transcription failure after preparation keeps prepared audio.
- delete removes raw leftovers and prepared audio.
