"""
Minimal LaTeX-to-PDF HTTP microservice.

Accepts a .tex file (and optionally other assets, e.g. images or .cls files)
via multipart/form-data, compiles it with xelatex inside an isolated temp
directory, and returns the resulting PDF — or a structured error containing
the compiler log if compilation fails.

Designed to be called from n8n's HTTP Request node over an internal Docker
network (no public port needed).
"""

import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from typing import List, Optional

app = FastAPI(title="LaTeX-to-PDF microservice")

# Which engine to invoke. xelatex is required for this CV template
# (it uses fontspec + \setmainfont, which pdflatex cannot process).
LATEX_ENGINE = os.environ.get("LATEX_ENGINE", "xelatex")

# Hard ceiling on compile time, so a malformed/infinite-loop .tex file
# can't hang a worker indefinitely.
COMPILE_TIMEOUT_SECONDS = int(os.environ.get("COMPILE_TIMEOUT_SECONDS", "60"))


@app.get("/health")
def health():
    """Simple liveness check, mirrors the pattern your other services use."""
    return {"status": "ok", "engine": LATEX_ENGINE}


@app.post("/convert")
async def convert(
    main_file: UploadFile = File(..., description="The main .tex file to compile"),
    assets: List[UploadFile] = File(
        default=[],
        description="Optional extra files (images, .cls, .sty) referenced by main_file",
    ),
):
    """
    Compile a .tex file to PDF.

    - main_file: the entry-point .tex file (e.g. cv.tex)
    - assets: any additional files it references, preserving relative paths
              is NOT supported in this minimal version — all assets are
              placed flat in the working directory. If your document
              references files in subfolders, extend this before relying
              on it for those cases.
    """
    if not main_file.filename.endswith(".tex"):
        raise HTTPException(
            status_code=400,
            detail="main_file must be a .tex file",
        )

    job_id = str(uuid.uuid4())
    work_dir = Path(tempfile.mkdtemp(prefix=f"latex_{job_id}_"))

    try:
        # Write the main .tex file
        main_path = work_dir / main_file.filename
        main_path.write_bytes(await main_file.read())

        # Write any additional assets alongside it
        if assets:
            for asset in assets:
                asset_path = work_dir / asset.filename
                asset_path.write_bytes(await asset.read())

        # Run the engine twice — this is necessary for documents with
        # references, tables of contents, or anything requiring a second
        # pass to resolve. Cheap insurance for a CV with little cost.
        compiler_output = ""
        pdf_path = work_dir / main_path.with_suffix(".pdf").name

        for pass_num in range(2):
            result = subprocess.run(
                [
                    LATEX_ENGINE,
                    "-interaction=nonstopmode",
                    "-halt-on-error",
                    main_path.name,
                ],
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=COMPILE_TIMEOUT_SECONDS,
            )
            compiler_output += (
                f"\n--- Pass {pass_num + 1} stdout ---\n{result.stdout}"
                f"\n--- Pass {pass_num + 1} stderr ---\n{result.stderr}"
            )

            if result.returncode != 0:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "LaTeX compilation failed",
                        "returncode": result.returncode,
                        "log": compiler_output,
                    },
                )

        if not pdf_path.exists():
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "Compiler reported success but no PDF was produced",
                    "log": compiler_output,
                },
            )

        # FileResponse streams the file and is fine for a one-shot job;
        # cleanup happens in the finally block after the response is sent
        # is NOT guaranteed with FileResponse + shutil.rmtree in `finally`
        # below, so we instead schedule cleanup via background task.
        return FileResponse(
            path=pdf_path,
            media_type="application/pdf",
            filename=pdf_path.name,
            background=_cleanup_task(work_dir),
        )

    except subprocess.TimeoutExpired:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(
            status_code=504,
            detail=f"Compilation exceeded {COMPILE_TIMEOUT_SECONDS}s timeout",
        )
    except HTTPException:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
    except Exception as e:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")


def _cleanup_task(work_dir: Path):
    from starlette.background import BackgroundTask

    def _remove():
        shutil.rmtree(work_dir, ignore_errors=True)

    return BackgroundTask(_remove)
