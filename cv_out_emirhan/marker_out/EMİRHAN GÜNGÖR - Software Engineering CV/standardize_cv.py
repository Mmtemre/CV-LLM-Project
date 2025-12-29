from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

try:
    import requests  # type: ignore
except Exception:  # pragma: no cover
    requests = None


SYSTEM_PROMPT = """You are a professional resume parser. Convert the following Markdown resume into a strictly valid JSON object following this schema exactly:

{
    "cv": {
        "name": string,
        "contact_info": {
            "email": string|null,
            "phone": string|null,
            "location": string|null,
            "website": string|null,
            "linkedin": string|null,
            "github": string|null
        },
        "experience": [
            {
                "company": string,
                "position": string|null,
                "location": string|null,
                "start_date": string|null,
                "end_date": string|null,
                "highlights": [string]
            }
        ],
        "education": [
            {
                "institution": string,
                "degree": string|null,
                "field": string|null,
                "location": string|null,
                "start_date": string|null,
                "end_date": string|null,
                "notes": [string]
            }
        ],
        "skills": [string]
    }
}

Rules:
- Output JSON only. No markdown. No commentary.
- Normalize dates to 'YYYY-MM' when possible. If unknown, use null.
- If a section was multi-column in the PDF, ensure items are associated with the correct parent entry.
- If a field is missing, use null (or [] for lists).
"""


@dataclass
class PipelinePaths:
    work_dir: Path
    marker_out_dir: Path
    markdown_path: Path
    structured_json_path: Path
    rendercv_yaml_path: Path


def _run(cmd: List[str], *, cwd: Optional[Path] = None) -> None:
    try:
        subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Command not found: {cmd[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Command failed ({exc.returncode}): {' '.join(cmd)}") from exc


def _resolve_executable(name: str) -> str:
    resolved = shutil.which(name)
    if resolved:
        return resolved

    # If we're in a venv, console scripts often live next to sys.executable.
    scripts_dir = Path(sys.executable).resolve().parent
    exe_name = name + (".exe" if os.name == "nt" else "")
    candidate = scripts_dir / exe_name
    if candidate.exists():
        return str(candidate)

    return name


def _find_best_markdown(marker_out_dir: Path) -> Path:
    candidates = list(marker_out_dir.rglob("*.md"))
    if not candidates:
        raise RuntimeError(f"Marker produced no .md files in: {marker_out_dir}")

    def score(p: Path) -> Tuple[int, int]:
        # Prefer longer files; prefer ones named like resume/cv.
        name_bias = 1 if re.search(r"(cv|resume)", p.name, re.IGNORECASE) else 0
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        return (name_bias, size)

    candidates.sort(key=score, reverse=True)
    return candidates[0]


def extract_markdown_with_marker(
    pdf_path: Path, marker_out_dir: Path, *, batch_multiplier: int = 2
) -> Path:
    marker_out_dir.mkdir(parents=True, exist_ok=True)
    # marker_single (marker-pdf 1.10+) expects output directory via --output_dir
    # and uses hyphenated option names. Older blog posts mention --batch_multiplier,
    # but that flag is not present in current releases; we map our batch_multiplier
    # to --layout_batch_size (best-effort) when provided.
    cmd: List[str] = [
        _resolve_executable("marker_single"),
        str(pdf_path),
        "--output_dir",
        str(marker_out_dir),
        "--output_format",
        "markdown",
    ]
    if batch_multiplier and batch_multiplier > 0:
        cmd.extend(["--layout_batch_size", str(batch_multiplier)])
    _run(cmd)
    return _find_best_markdown(marker_out_dir)


def _extract_json_object(text: str) -> Dict[str, Any]:
    text = text.strip()
    # If model already returned pure JSON, parse directly.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Otherwise, attempt to salvage by extracting the outermost JSON object.
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Model output does not contain a JSON object")

    candidate = text[start : end + 1]
    return json.loads(candidate)


def ollama_markdown_to_structured_json(
    markdown_text: str,
    *,
    model: str = "qwen3:8b",
    base_url: str = "http://localhost:11434",
    timeout_s: int = 180,
) -> Dict[str, Any]:
    if requests is None:
        raise RuntimeError(
            "Missing dependency 'requests'. Install it or use requirements.txt."
        )

    url = base_url.rstrip("/") + "/api/chat"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": markdown_text},
        ],
        "stream": False,
        # Many Ollama builds/models honor this; if ignored, we still salvage JSON.
        "format": "json",
        "options": {
            "temperature": 0,
        },
    }

    resp = requests.post(url, json=payload, timeout=timeout_s)
    resp.raise_for_status()
    data = resp.json()

    content = (
        (data.get("message") or {}).get("content")
        if isinstance(data, dict)
        else None
    )
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("Ollama response missing message.content")

    obj = _extract_json_object(content)
    if not isinstance(obj, dict) or "cv" not in obj:
        raise RuntimeError("Structured JSON missing top-level 'cv' key")

    return obj


def _strip_none_fields(d: Dict[str, Any]) -> Dict[str, Any]:
    # Keep schema explicit, but RenderCV YAML often prefers absence over null.
    out: Dict[str, Any] = {}
    for k, v in d.items():
        if v is None:
            continue
        out[k] = v
    return out


def convert_structured_to_rendercv_yaml(structured: Dict[str, Any]) -> Dict[str, Any]:
    cv = structured.get("cv") or {}
    contact = cv.get("contact_info") or {}

    rendercv_cv: Dict[str, Any] = {
        "name": cv.get("name") or "",
        # RenderCV common fields
        "email": contact.get("email"),
        "phone": contact.get("phone"),
        "location": contact.get("location"),
        "website": contact.get("website"),
    }

    social: List[Dict[str, str]] = []
    if contact.get("linkedin"):
        social.append({"network": "LinkedIn", "username": str(contact["linkedin"])})
    if contact.get("github"):
        social.append({"network": "GitHub", "username": str(contact["github"])})
    if social:
        rendercv_cv["social_networks"] = social

    sections: Dict[str, Any] = {}

    # Experience
    exp_entries = []
    for item in cv.get("experience") or []:
        if not isinstance(item, dict):
            continue
        exp_entries.append(
            _strip_none_fields(
                {
                    "company": item.get("company") or "",
                    "position": item.get("position"),
                    "location": item.get("location"),
                    "start_date": item.get("start_date"),
                    "end_date": item.get("end_date"),
                    "highlights": item.get("highlights") or [],
                }
            )
        )
    if exp_entries:
        sections["experience"] = exp_entries

    # Education
    edu_entries = []
    for item in cv.get("education") or []:
        if not isinstance(item, dict):
            continue
        edu_entries.append(
            _strip_none_fields(
                {
                    "institution": item.get("institution") or "",
                    "degree": item.get("degree"),
                    "field": item.get("field"),
                    "location": item.get("location"),
                    "start_date": item.get("start_date"),
                    "end_date": item.get("end_date"),
                    "highlights": item.get("notes") or [],
                }
            )
        )
    if edu_entries:
        sections["education"] = edu_entries

    # Skills
    skills = [s for s in (cv.get("skills") or []) if isinstance(s, str) and s.strip()]
    if skills:
        sections["skills"] = [{"details": ", ".join(skills)}]

    rendercv = {
        "cv": _strip_none_fields(rendercv_cv),
    }

    if sections:
        rendercv["cv"]["sections"] = sections

    return rendercv


def write_yaml(path: Path, data: Dict[str, Any]) -> None:
    if yaml is None:
        raise RuntimeError("Missing dependency 'pyyaml'. Install it or use requirements.txt.")

    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)


def render_with_rendercv(
    rendercv_yaml: Path,
    *,
    cwd: Optional[Path] = None,
) -> None:
    cmd = [_resolve_executable("rendercv"), "render", str(rendercv_yaml)]
    _run(cmd, cwd=cwd)


def build_paths(work_dir: Path) -> PipelinePaths:
    marker_out_dir = work_dir / "marker_out"
    return PipelinePaths(
        work_dir=work_dir,
        marker_out_dir=marker_out_dir,
        markdown_path=work_dir / "resume.md",
        structured_json_path=work_dir / "resume_data.json",
        rendercv_yaml_path=work_dir / "resume_data.rendercv.yaml",
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Standardize a messy CV: PDF -> Markdown (Marker) -> JSON (Ollama) -> RenderCV YAML -> PDF (RenderCV)."
    )
    parser.add_argument("pdf", type=str, help="Input PDF path")
    parser.add_argument(
        "--work-dir",
        type=str,
        default="cv_out",
        help="Working directory for intermediate outputs (default: cv_out)",
    )
    parser.add_argument(
        "--batch-multiplier",
        type=int,
        default=2,
        help="Marker batch multiplier (default: 2)",
    )
    parser.add_argument(
        "--ollama-model",
        type=str,
        default="qwen3:8b",
        help="Ollama model name (default: qwen3:8b)",
    )
    parser.add_argument(
        "--ollama-url",
        type=str,
        default="http://localhost:11434",
        help="Ollama base URL (default: http://localhost:11434)",
    )
    parser.add_argument(
        "--skip-render",
        action="store_true",
        help="Only produce Markdown/JSON/YAML; do not run RenderCV",
    )

    args = parser.parse_args(argv)

    pdf_path = Path(args.pdf).expanduser().resolve()
    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")

    work_dir = Path(args.work_dir).expanduser().resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    paths = build_paths(work_dir)

    # Phase 1: Marker
    md_path = extract_markdown_with_marker(
        pdf_path, paths.marker_out_dir, batch_multiplier=args.batch_multiplier
    )
    md_text = md_path.read_text(encoding="utf-8", errors="replace")
    paths.markdown_path.write_text(md_text, encoding="utf-8")

    # Phase 2: Ollama -> structured JSON
    structured = ollama_markdown_to_structured_json(
        md_text, model=args.ollama_model, base_url=args.ollama_url
    )
    paths.structured_json_path.write_text(
        json.dumps(structured, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Convert to RenderCV YAML
    rendercv_yaml_obj = convert_structured_to_rendercv_yaml(structured)
    write_yaml(paths.rendercv_yaml_path, rendercv_yaml_obj)

    # Phase 3: RenderCV
    if not args.skip_render:
        render_with_rendercv(paths.rendercv_yaml_path, cwd=work_dir)

    print("OK")
    print(f"Markdown:   {paths.markdown_path}")
    print(f"JSON:       {paths.structured_json_path}")
    print(f"RenderCV:   {paths.rendercv_yaml_path}")
    if not args.skip_render:
        print("Rendered:   (see RenderCV output in work dir)")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
