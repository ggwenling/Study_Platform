"""Verify and repair saved DOCX table columns in a local study database."""

import argparse
import hashlib
from collections import defaultdict
from io import BytesIO
from zipfile import BadZipFile

import sqlalchemy as sa
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml.exceptions import InvalidXmlError
from lxml.etree import XMLSyntaxError
from sqlalchemy.engine import make_url

from study_platform.core.config import get_settings
from study_platform.persistence.database import session_factory
from study_platform.persistence.models import (
    IndexVersion,
    MaterialChunk,
    MaterialSection,
    MaterialVersion,
    MessageCitation,
)
from study_platform.services.ingestion import chunks, extract_docx
from study_platform.services.storage import source_path

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
LOOPBACK = {"127.0.0.1", "::1"}


class RepairSkipped(ValueError):
    pass


def verified_changes(db, version):
    try:
        raw = source_path(version.id).read_bytes()
    except OSError:
        raise RepairSkipped("原件不可读取") from None
    if hashlib.sha256(raw).hexdigest() != version.source_sha256:
        raise RepairSkipped("原件哈希不匹配")
    try:
        corrected = list(extract_docx(BytesIO(raw)))
        # Versions without omitted leading columns do not need any updates.
        legacy = list(extract_docx(BytesIO(raw), legacy_columns=True))
    except (BadZipFile, PackageNotFoundError, InvalidXmlError, XMLSyntaxError, KeyError, ValueError):
        raise RepairSkipped("原件无法解析") from None
    if corrected == legacy:
        return []
    if version.parser_profile != "document-v1" or [x[0] for x in corrected] != [
        x[0] for x in legacy
    ]:
        raise RepairSkipped("解析版本或段落不匹配")

    changes = []

    def verify_locator(row, old, new):
        if row.locator != new:
            if row.locator != old:
                raise RepairSkipped("存储的定位与原件不匹配")
            changes.append((row, new))

    sections = db.scalars(
        sa.select(MaterialSection)
        .where(
            MaterialSection.material_version_id == version.id,
        )
        .order_by(MaterialSection.ordinal)
    ).all()
    if len(sections) != len(corrected):
        raise RepairSkipped("段落数量不匹配")
    expected_chunks = []
    for ordinal, (section, (text, old), (_, new)) in enumerate(
        zip(sections, legacy, corrected, strict=True)
    ):
        if section.ordinal != ordinal or section.text != text:
            raise RepairSkipped("段落顺序或内容不匹配")
        verify_locator(section, old, new)
        expected_chunks.extend((section.id, value, old, new) for value in chunks(text))

    by_index = defaultdict(list)
    for chunk, profile in db.execute(
        sa.select(MaterialChunk, IndexVersion.pipeline_profile)
        .join(
            IndexVersion,
            IndexVersion.id == MaterialChunk.index_version_id,
        )
        .where(MaterialChunk.material_version_id == version.id)
        .order_by(MaterialChunk.ordinal)
    ):
        if profile != "document-v1":
            raise RepairSkipped("分块版本不匹配")
        by_index[chunk.index_version_id].append(chunk)
    if not by_index:
        raise RepairSkipped("缺少可核对的索引分块")
    verified = {}
    for index_chunks in by_index.values():
        if len(index_chunks) != len(expected_chunks):
            raise RepairSkipped("分块数量不匹配")
        for ordinal, (chunk, (section_id, value, old, new)) in enumerate(
            zip(index_chunks, expected_chunks, strict=True)
        ):
            digest = hashlib.sha256(value.encode()).hexdigest()
            if (
                chunk.ordinal != ordinal
                or chunk.section_id != section_id
                or chunk.text != value
                or chunk.text_sha256 != digest
            ):
                raise RepairSkipped("分块关联、内容或哈希不匹配")
            verify_locator(chunk, old, new)
            verified[chunk.id] = (digest, old, new)

    for citation in db.scalars(
        sa.select(MessageCitation).where(
            MessageCitation.material_version_id == version.id,
        )
    ):
        if citation.chunk_id not in verified:
            raise RepairSkipped("找不到引用对应的分块")
        digest, old, new = verified[citation.chunk_id]
        if citation.quote_sha256 != digest:
            raise RepairSkipped("引用哈希不匹配")
        verify_locator(citation, old, new)
    return changes


def repair(factory, *, apply=False):
    with factory() as db:
        versions = db.scalars(
            sa.select(MaterialVersion.id)
            .where(
                MaterialVersion.source_mime == DOCX,
                MaterialVersion.parse_status == "ready",
            )
            .order_by(MaterialVersion.id)
        ).all()

    changed = rows = skipped = 0
    for version_id in versions:
        try:
            with factory.begin() as db:
                changes = verified_changes(db, db.get(MaterialVersion, version_id))
                if apply:
                    for row, locator in changes:
                        row.locator = locator
        except RepairSkipped as error:
            print(f"跳过版本 {version_id}，{error}")
            skipped += 1
            continue
        if changes:
            changed += 1
            rows += len(changes)
            print(f"版本 {version_id}，{'已修正' if apply else '可修正'} {len(changes)} 条定位")
    return {
        "checked": len(versions),
        "changed_versions": changed,
        "changed_rows": rows,
        "skipped": skipped,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write verified corrections after stopping API and worker",
    )
    parser.add_argument(
        "--database", help="required with --apply, exact name of the configured local database"
    )
    args = parser.parse_args()
    try:
        url = make_url(get_settings().database_url)
        if (
            url.get_backend_name() != "postgresql"
            or url.host not in LOOPBACK | {"localhost"}
            or {"host", "hostaddr", "dbname", "service", "servicefile"} & set(url.query)
        ):
            raise ValueError("修复命令仅接受明确指定的本机 PostgreSQL 地址")
        if args.apply and (not args.database or args.database != url.database):
            raise ValueError("--apply 需要 --database 与配置的数据库名称一致")
        factory = session_factory()
        with factory() as db:
            name, address, schema = db.execute(
                sa.text(
                    "SELECT current_database(), host(inet_server_addr()), to_regnamespace('study') IS NOT NULL"
                )
            ).one()
            if name != url.database or address not in LOOPBACK or not schema:
                raise ValueError("实际数据库地址、名称或 study schema 与预期不符")
        print(f"数据库 {name}，{'应用' if args.apply else '预览'}")
        result = repair(factory, apply=args.apply)
        print(
            f"检查 {result['checked']} 个版本，{'已修正' if args.apply else '可修正'} "
            f"{result['changed_versions']} 个版本 / {result['changed_rows']} 条定位，跳过 {result['skipped']} 个版本"
        )
    except ValueError as error:
        raise SystemExit(str(error)) from None
    except Exception as error:
        raise SystemExit(
            f"定位修复中断（{type(error).__name__}），已提交的版本可安全复查"
        ) from None


if __name__ == "__main__":
    main()
