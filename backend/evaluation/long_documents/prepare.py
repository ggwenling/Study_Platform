"""Generate a deterministic public long-document corpus and its 20 question records."""

import argparse
import hashlib
import io
import json
from pathlib import Path

from docx import Document
from PIL import Image, ImageDraw, ImageFont


def write_pdf(path, lines, scanned, *, large=False):
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    page_ids = []

    def stream(payload, attributes=b""):
        objects.append(b"<< /Length %d %s >>\nstream\n" % (len(payload), attributes) + payload + b"\nendstream")
        return len(objects)

    for number, text in enumerate(lines, 1):
        resources = b"<< /Font << /F1 3 0 R >> >>"
        if scanned(number):
            with Image.new("L", (920, 230), 255) as image:
                draw = ImageDraw.Draw(image)
                try:
                    font = ImageFont.truetype("DejaVuSans.ttf", 27)
                except OSError:
                    font = ImageFont.load_default(size=27)
                draw.multiline_text((18, 36), text, font=font, spacing=24, fill=0)
                jpeg = io.BytesIO()
                image.save(jpeg, format="JPEG", quality=88)
            image_id = stream(jpeg.getvalue(),
                              b"/Type /XObject /Subtype /Image /Width 920 /Height 230 "
                              b"/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /DCTDecode")
            resources = b"<< /XObject << /Im %d 0 R >> >>" % image_id
            contents = stream(b"q 460 0 0 115 0 0 cm /Im Do Q")
        else:
            body = []
            for line in text.splitlines():
                raw = line.encode("ascii").replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
                body.append(b"(" + raw + b") Tj T*")
            contents = stream(b"BT /F1 13 Tf 18 TL 14 92 Td " + b" ".join(body) + b" ET")
        objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 460 115] "
                       b"/Resources %s /Contents %d 0 R >>" % (resources, contents))
        page_ids.append(len(objects))

    objects[1] = b"<< /Type /Pages /Count %d /Kids [%s] >>" % (
        len(page_ids), b" ".join(b"%d 0 R" % key for key in page_ids))
    if large:
        stream(b" " * (21 * 1024**2))
    with path.open("wb") as output:
        output.write(b"%PDF-1.4\n")
        offsets = []
        for index, obj in enumerate(objects, 1):
            offsets.append(output.tell())
            output.write(b"%d 0 obj\n" % index + obj + b"\nendobj\n")
        xref = output.tell()
        output.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
        for offset in offsets:
            output.write(b"%010d 00000 n \n" % offset)
        output.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref))


def prepare(output):
    output.mkdir(parents=True, exist_ok=True)
    source = output / "original"
    source.mkdir(exist_ok=True)
    materials, questions = [], []

    def register(name, fmt):
        file = source / f"{name}.{fmt}"
        materials.append({"id": name, "title": name, "format": fmt, "knowledge_base": "A",
                          "path": file.relative_to(output).as_posix()})
        return file

    def question(name, code, prompt, answer, quote, **location):
        questions.append({"id": f"LONG-{len(questions) + 1:02d}", "fact_group": code, "split": "heldout",
                          "knowledge_base": "A", "question": prompt, "answerable": True,
                          "expected_facts": [[answer]], "support": [{"document": name, "quote": quote}],
                          "fixture_location": location})

    for name, facts, mode in (
        ("native-guide", [(3, "BIRCH-203", "62 minutes"), (80, "CEDAR-280", "47 meters"),
                          (150, "MAPLE-350", "92 liters")], "text"),
        ("scan-register", [(24, "LARCH-424", "38 minutes"), (81, "ASH-481", "64 meters"),
                           (150, "ELM-550", "73 liters")], "scan"),
        ("mixed-manual", [(18, "OAK-618", "57 minutes"), (72, "PINE-672", "86 meters"),
                          (123, "IVY-723", "41 liters"), (149, "ALDER-749", "35 minutes")], "mixed"),
    ):
        special = {page: f"{code} is assigned {answer}." for page, code, answer in facts}
        lines = [f"Operational reference manual. Page {page:03d}.\n"
                 + special.get(page, "Routine reference record. Confirm the entry before use.")
                 for page in range(1, 151)]
        write_pdf(register(name, "pdf"), lines,
                  lambda page: mode == "scan" or (mode == "mixed" and page in (72, 149)),
                  large=mode == "mixed")
        for page, code, answer in facts:
            question(name, code, f"手册条目 {code} 的核定值是多大？请标明单位。", answer, special[page], page=page)

    name = "field-table"
    doc = Document()
    fact_rows = {
        41: ("TIDAL-841", "orange valve"),
        104: ("RIVER-904", "north cabinet"),
        171: ("DELTA-971", "silver bracket"),
        242: ("WATER-042", "steel nozzle"),
        318: ("CANAL-118", "green connector"),
    }
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Field code"
    table.rows[0].cells[1].text = "Assigned component"
    for ordinal in range(1, 351):
        code, value = fact_rows.get(ordinal, (f"RESERVE-{ordinal:04d}", "unallocated"))
        cells = table.add_row().cells
        cells[0].text, cells[1].text = code, value
        if ordinal in fact_rows:
            question(name, code, f"现场表格中的 {code} 对应哪个部件或位置？保留原始英文名称。", value,
                     value, table=1, row=ordinal + 1)
    doc.save(register(name, "docx"))

    name = "operations-ledger"
    facts = {210: ("RIDGE-210", "96 seconds"), 411: ("VALLEY-411", "71 seconds"),
             674: ("CREST-674", "53 seconds"), 901: ("SUMMIT-901", "84 seconds"),
             1190: ("SLOPE-1190", "67 seconds")}
    text_lines = []
    for line in range(1, 1201):
        if line not in facts:
            text_lines.append(f"Operational archive line {line:04d}. Check the current edition before scheduling.")
            continue
        code, value = facts[line]
        quote = f"Routine confirmation for entry {code} requires a waiting period of {value}."
        text_lines.append(quote)
        question(name, code, f"运行台账里的 {code} 等待期是多久？请给出原文单位。", value, quote, line=line)
    register(name, "txt").write_text("\n".join(text_lines) + "\n", encoding="utf-8")
    for current, related in ((questions[2], questions[0]), (questions[9], questions[7]), (questions[19], questions[12])):
        current["question"] += " 另外，" + related["question"]
        current["expected_facts"] += related["expected_facts"]
        current["support"] += related["support"]
    for item in materials:
        item["sha256"] = hashlib.sha256((output / item["path"]).read_bytes()).hexdigest()
    (output / "materials.json").write_text(json.dumps(materials, indent=2), encoding="utf-8")
    (output / "questions.jsonl").write_text("".join(json.dumps(q, ensure_ascii=False) + "\n" for q in questions),
                                            encoding="utf-8")
    write_pdf(output / "over-pages.pdf", ["Page outside the accepted boundary."] * 151, lambda _: False)
    assert len(questions) == 20


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    prepare(parser.parse_args().output)
