"""Bounded public-source/layout diagnostic; not protocol extraction or admission."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    with path.open("x") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def main():
    if not os.environ.get("SLURM_JOB_ID"):
        raise RuntimeError("Run document conversion and downloads in Slurm")
    project = Path(os.environ["ATLAS_PROJECT_ROOT"])
    base = project / "technology_atlas/v2/work/benchmark_20261020"
    output = base / "extra_source_inspection_01"
    output.mkdir()
    original = base / "reference_v1_01/work/sources/acquisition/methods/spatial-transcriptomics/supplements/stahl2016_supplement_compound_reprint.pdf"
    assert sha(original) == "39d88dc581bc348d28d7355273c6b66c9cecf1c62c0c1a3a26604bd042825731"
    text_path = base / "spatial_boundary_inspection_01/stahl2016_supplement_compound_reprint/full_pdf_text.txt"
    assert sha(text_path) == "1ea61bf7362cc84cd939d46d4dbe446fcb2013ae03f4f1ab7e912e5a5b9b38b0"
    pages = text_path.read_text().split("\f")
    if pages[-1] == "":
        pages.pop()
    assert len(pages) == 45
    executable = Path(shutil.which("pdftoppm")).resolve()
    command = [str(executable), "-r", "64", "-png", str(original), str(output / "page")]
    subprocess.run(command, check=True, timeout=300, capture_output=True)
    rows = [{"page": number, "image": str(output / f"page-{number:02d}.png"),
             "image_sha256": sha(output / f"page-{number:02d}.png"), "text": text}
            for number, text in enumerate(pages, 1)]
    # Higher resolution for the main article and the SI identity/Methods opening.
    details = output / "detail"
    details.mkdir()
    detail_command = [str(executable), "-f", "1", "-l", "12", "-r", "110", "-png", str(original), str(details / "page")]
    subprocess.run(detail_command, check=True, timeout=300, capture_output=True)
    manifest = {"original": str(original), "original_sha256": sha(original), "pages": rows,
                "source_text": {"path": str(text_path), "sha256": sha(text_path)},
                "commands": [command, detail_command], "executable_sha256": sha(executable),
                "driver_sha256": sha(__file__), "slurm_job_id": os.environ["SLURM_JOB_ID"],
                "boundary": "Rendered inspection inputs only. No visual review, source purity or scientific completeness asserted."}
    write(output / "render_manifest.json", manifest)
    try:
        from PIL import Image, ImageOps, ImageDraw
    except ImportError:
        pass
    else:
        for first in range(1, 46, 9):
            sheet = Image.new("RGB", (1500, 2205), "#dadada")
            drawing = ImageDraw.Draw(sheet)
            for offset, number in enumerate(range(first, min(first + 9, 46))):
                with Image.open(output / f"page-{number:02d}.png") as source:
                    tile = ImageOps.contain(source.convert("RGB"), (490, 700))
                    x, y = (offset % 3) * 500, (offset // 3) * 735
                    sheet.paste(tile, (x, y + 25))
                    drawing.text((x + 8, y + 5), f"PDF page {number}", fill="black")
            sheet.save(output / f"contact-{first:02d}.png")
    # This exact original SI hyperlink was observed on the author's Broad page.
    from genomics_discovery.curation.source_acquisition.downloads import download
    url = "http://science.sciencemag.org/content/sci/suppl/2019/03/27/363.6434.1463.DC1/aaw1219_Rodriques_SM.pdf"
    try:
        fetched = download(url)
        target = output / "slide_original_science_supplement.pdf"
        with target.open("xb") as handle:
            handle.write(fetched.content)
        result = {"status": "downloaded_unregistered", "requested_url": url,
                  "final_url": fetched.final_url, "content_type": fetched.content_type,
                  "path": str(target), "sha256": sha(target), "bytes": target.stat().st_size}
    except (OSError, ValueError) as exc:
        result = {"status": "unavailable_checked_route", "requested_url": url,
                  "error": type(exc).__name__ + ": " + str(exc)}
    write(output / "slide_retrieval.json", result)
    print(json.dumps({"manifest": str(output / "render_manifest.json"), "rendered_pages": len(rows),
                      "slide_retrieval": result}))


if __name__ == "__main__":
    main()
