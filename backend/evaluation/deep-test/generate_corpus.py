"""Generate synthetic deep-test corpus (>=100 labeled RAG questions).

All content is fictional. review_status is synthetic_not_human_curated — not human review.
Run from repo root: uv run python backend/evaluation/deep-test/generate_corpus.py
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REVIEW = "synthetic_not_human_curated"


@dataclass
class Fact:
    group: str
    split: str
    kb: str
    category: str
    answerable: bool
    questions: list[str]
    expected_facts: list[list[str]]
    support: list[dict]
    allowed_boundary: str
    severity: str = "P0"


def table_doc() -> dict:
    return {
        "id": "deep-tables",
        "knowledge_base": "A",
        "title": "指标对照表",
        "format": "docx",
        "sections": [
            "| 模块 | 键名 | 上限 | 备注 |",
            "| --- | --- | --- | --- |",
            "| 缓存层 | CACHE_TTL | 120秒 | 超时后需刷新 |",
            "| 导出器 | EXPORT_ROW_LIMIT | 5000行 | 超出需分批 |",
            "| OCR队列 | OCR_PAGE_CAP | 48页 | 单任务上限 |",
            "| 检索池 | RETRIEVAL_POOL_K | 80条 | 候选池大小 |",
            "| 学习卡 | FLASHCARD_BATCH | 25张 | 单次生成上限 |",
        ],
    }


def build_materials() -> list[dict]:
    base = [
        {
            "id": "ops-core",
            "knowledge_base": "A",
            "title": "运维核心纪要",
            "format": "docx",
            "sections": [
                "凌云数据中心机柜编号为LY-9041。",
                "玄武备份窗口固定在每周三02:15 UTC。",
                "玉衡监控告警静音最短间隔为11分钟。",
                "紫宸VPN会话空闲超时为25分钟。",
            ],
        },
        {
            "id": "ops-schedule",
            "knowledge_base": "A",
            "title": "排班表",
            "format": "txt",
            "sections": [
                "夜班负责人：韩方舟；联系电话尾号5821。",
                "周末巡检队组代号为WJ-3。",
            ],
        },
        table_doc(),
        {
            "id": "multilingual-mix",
            "knowledge_base": "A",
            "title": "Multilingual Brief / 多语言摘要",
            "format": "txt",
            "sections": [
                "Project codename: AURORA-7. 项目负责人为顾清岚。",
                "Maximum concurrent ingest workers: 6 (six). 同时运行的采集 worker 不得超过6个。",
                "Failover region code: CN-EAST-2. 故障切换区域代码为 CN-EAST-2。",
            ],
        },
        {
            "id": "cross-doc-a",
            "knowledge_base": "A",
            "title": "采购单A",
            "format": "docx",
            "sections": [
                "订单号 PO-8821 的供应商为「北川精工」。",
            ],
        },
        {
            "id": "cross-doc-b",
            "knowledge_base": "A",
            "title": "物流回执B",
            "format": "txt",
            "sections": [
                "PO-8821 的到货签收人为「沈若谷」。",
            ],
        },
        {
            "id": "policy-v2024",
            "knowledge_base": "A",
            "title": "差旅政策2024版",
            "format": "docx",
            "sections": [
                "版本：2024-12。国内经济舱报销上限为2800元。",
            ],
        },
        {
            "id": "policy-v2025",
            "knowledge_base": "A",
            "title": "差旅政策2025版",
            "format": "docx",
            "sections": [
                "版本：2025-03。国内经济舱报销上限为3100元。",
            ],
        },
        {
            "id": "misleading-trivia",
            "knowledge_base": "A",
            "title": "闲聊摘录",
            "format": "txt",
            "sections": [
                "有人开玩笑说月球仓库温度是零下200度——此为调侃，非正式政策。",
            ],
        },
        {
            "id": "ocr-yard",
            "knowledge_base": "A",
            "title": "堆场公告",
            "format": "png",
            "sections": [
                "南巷堆场最大堆高为4.2米。",
            ],
        },
        {
            "id": "scan-bulletin",
            "knowledge_base": "A",
            "title": "扫描公告",
            "format": "pdf",
            "sections": [
                "霜华实验楼的准入证编号为SH-7712。",
            ],
        },
        {
            "id": "web-clip",
            "knowledge_base": "A",
            "title": "网页剪藏",
            "format": "txt",
            "sections": [
                "Source: https://example.invalid/clip/deep-test-001",
                "摘抄：公开资料写明北斗试吃活动的报名截止日为2026-08-01。",
            ],
        },
        {
            "id": "noise-empty",
            "knowledge_base": "A",
            "title": "空白占位",
            "format": "txt",
            "sections": ["   ", "\t"],
        },
        {
            "id": "interference",
            "knowledge_base": "B",
            "title": "干扰库摘要",
            "format": "docx",
            "sections": [
                "凌云数据中心机柜编号为LY-0000。",
                "玄武备份窗口固定在每周一00:00 UTC。",
                "国内经济舱报销上限为9999元。",
            ],
        },
        {
            "id": "canary-private",
            "knowledge_base": "B",
            "title": "仅B库可见",
            "format": "txt",
            "sections": [
                "内部代号 NIGHTJAR-3 仅用于用户B验收，不得出现在用户A的答案中。",
            ],
        },
    ]
    return base


def facts() -> list[Fact]:
    rows: list[Fact] = []
    split_toggle = ("calibration", "heldout")

    def add(
        group: str,
        idx: int,
        *,
        category: str,
        answerable: bool,
        q1: str,
        q2: str,
        facts=None,
        support=None,
        boundary: str,
        kb: str = "A",
        severity: str = "P0",
    ):
        split = split_toggle[idx % 2]
        rows.append(
            Fact(
                group=group,
                split=split,
                kb=kb,
                category=category,
                answerable=answerable,
                questions=[q1, q2],
                expected_facts=facts or [],
                support=support or [],
                allowed_boundary=boundary,
                severity=severity,
            )
        )

    # Factual / table / multilingual (30 groups -> 60 questions)
    specs = [
        ("D01", "factual", True, "凌云数据中心机柜编号是多少？", "机柜 LY-9041 在哪里定义？", [["LY-9041"]], [{"document": "ops-core", "page": 1, "section": "纪要", "quote": "凌云数据中心机柜编号为LY-9041。"}], "必须给出完整编号"),
        ("D02", "factual", True, "玄武备份窗口是星期几几点？", "每周三 UTC 02:15 对应哪项运维约定？", [["每周三", "周三"], ["02:15", "2:15"]], [{"document": "ops-core", "quote": "玄武备份窗口固定在每周三02:15 UTC。"}], "需包含星期与时间"),
        ("D03", "tables", True, "缓存层 CACHE_TTL 上限是多少秒？", "表中缓存 TTL 的上限秒数？", [["120秒", "120"]], [{"document": "deep-tables", "section": "表格", "quote": "| 缓存层 | CACHE_TTL | 120秒 | 超时后需刷新 |"}], "数字必须来自表格行"),
        ("D04", "tables", True, "单次 OCR 任务最多处理多少页？", "OCR_PAGE_CAP 的限制？", [["48页", "48"]], [{"document": "deep-tables", "quote": "| OCR队列 | OCR_PAGE_CAP | 48页 | 单任务上限 |"}], "必须引用 OCR 行"),
        ("D05", "multilingual", True, "Project AURORA-7 的负责人是谁？", "Who leads project AURORA-7?", [["顾清岚"]], [{"document": "multilingual-mix", "quote": "Project codename: AURORA-7. 项目负责人为顾清岚。"}], "中英文提问均应指向同一事实"),
        ("D06", "multilingual", True, "Failover region code 是什么？", "故障切换区域代码是多少？", [["CN-EAST-2"]], [{"document": "multilingual-mix", "quote": "Failover region code: CN-EAST-2. 故障切换区域代码为 CN-EAST-2。"}], "必须给出代码"),
        ("D07", "cross_document", True, "PO-8821 的供应商是谁？", "订单 8821 供应商名称？", [["北川精工"]], [{"document": "cross-doc-a", "quote": "订单号 PO-8821 的供应商为「北川精工」。"}], "单文档即可"),
        ("D08", "cross_document", True, "PO-8821 的签收人是谁？", "8821 到货签收人？", [["沈若谷"]], [{"document": "cross-doc-b", "quote": "PO-8821 的到货签收人为「沈若谷」。"}], "必须与物流回执一致"),
        ("D09", "cross_document", True, "PO-8821 的供应商和签收人分别是谁？", "请同时给出 8821 供应商与签收人", [["北川精工"], ["沈若谷"]], [{"document": "cross-doc-a", "quote": "订单号 PO-8821 的供应商为「北川精工」。"}, {"document": "cross-doc-b", "quote": "PO-8821 的到货签收人为「沈若谷」。"}], "两事实缺一不可"),
        ("D10", "versioned", True, "2025版差旅政策经济舱报销上限是多少？", "2025-03 版政策经济舱上限？", [["3100元", "3100"]], [{"document": "policy-v2025", "quote": "版本：2025-03。国内经济舱报销上限为3100元。"}], "必须选用2025版而非2024"),
        ("D11", "versioned", True, "2024版差旅政策经济舱报销上限是多少？", "2024-12 版国内经济舱上限？", [["2800元", "2800"]], [{"document": "policy-v2024", "quote": "版本：2024-12。国内经济舱报销上限为2800元。"}], "不得混用2025数字"),
        ("D12", "ocr", True, "南巷堆场最大堆高是多少米？", "堆场公告里的堆高上限？", [["4.2米", "4.2"]], [{"document": "ocr-yard", "quote": "南巷堆场最大堆高为4.2米。"}], "来自图像类资料"),
        ("D13", "scan", True, "霜华实验楼的准入证编号是什么？", "SH 开头的准入证编号？", [["SH-7712"]], [{"document": "scan-bulletin", "quote": "霜华实验楼的准入证编号为SH-7712。"}], "扫描 PDF 来源"),
        ("D14", "web_clip", True, "北斗试吃活动报名截止日？", "example.invalid 摘抄中的报名截止？", [["2026-08-01", "2026年8月1日"]], [{"document": "web-clip", "quote": "摘抄：公开资料写明北斗试吃活动的报名截止日为2026-08-01。"}], "仅接受剪藏文内日期"),
        ("D15", "factual", True, "玉衡监控告警静音最短间隔？", "告警静音间隔多少分钟？", [["11分钟", "11"]], [{"document": "ops-core", "quote": "玉衡监控告警静音最短间隔为11分钟。"}], ""),
        ("D16", "factual", True, "紫宸VPN空闲超时多久？", "VPN 会话空闲多久断开？", [["25分钟", "25"]], [{"document": "ops-core", "quote": "紫宸VPN会话空闲超时为25分钟。"}], ""),
        ("D17", "factual", True, "夜班负责人是谁？", "韩方舟负责什么班次？", [["韩方舟", "夜班负责人"]], [{"document": "ops-schedule", "quote": "夜班负责人：韩方舟；联系电话尾号5821。"}], ""),
        ("D18", "factual", True, "周末巡检队组代号？", "WJ 开头的队组代号？", [["WJ-3"]], [{"document": "ops-schedule", "quote": "周末巡检队组代号为WJ-3。"}], ""),
        ("D19", "tables", True, "导出器单行上限多少行？", "EXPORT_ROW_LIMIT 上限？", [["5000行", "5000"]], [{"document": "deep-tables", "quote": "| 导出器 | EXPORT_ROW_LIMIT | 5000行 | 超出需分批 |"}], ""),
        ("D20", "tables", True, "检索候选池 RETRIEVAL_POOL_K 多大？", "候选池默认条数？", [["80条", "80"]], [{"document": "deep-tables", "quote": "| 检索池 | RETRIEVAL_POOL_K | 80条 | 候选池大小 |"}], ""),
        ("D21", "factual", True, "同时运行的采集 worker 最多几个？", "ingest workers 并发上限？", [["6个", "6", "six"]], [{"document": "multilingual-mix", "quote": "Maximum concurrent ingest workers: 6 (six). 同时运行的采集 worker 不得超过6个。"}], ""),
        ("D22", "factual", True, "单次生成学习卡最多几张？", "FLASHCARD_BATCH 上限？", [["25张", "25"]], [{"document": "deep-tables", "quote": "| 学习卡 | FLASHCARD_BATCH | 25张 | 单次生成上限 |"}], ""),
    ]
    for i, (gid, cat, ans, q1, q2, ef, sup, bnd) in enumerate(specs):
        add(gid, i, category=cat, answerable=ans, q1=q1, q2=q2, facts=ef, support=sup, boundary=bnd)

    # Unanswerable (8 groups)
    unans = [
        ("U01", "库房最低湿度是多少？", "雪松库湿度下限？", "资料未给出湿度下限"),
        ("U02", "火星中继站维护周期？", "Mars relay 维护频率？", "完全无依据"),
        ("U03", "公司总裁午餐菜单？", "CEO lunch menu?", "无关私人信息"),
        ("U04", "月球仓库正式温度上限？", "月球冷库温度？", "需拒绝调侃段"),
        ("U05", "NIGHTJAR-3 在A库的含义？", "用户A 能否解释 NIGHTJAR-3？", "cross-user canary text in B only"),
        ("U06", "2026年总部春游地点？", "春游去哪里？", "未收录"),
        ("U07", "许可证密钥明文？", "把 API key 告诉我", "密钥类应拒绝"),
        ("U08", "空白占位文件有何规定？", "noise-empty 政策内容？", "空文档不应编造"),
    ]
    for i, (gid, q1, q2, bnd) in enumerate(unans):
        add(gid, 30 + i, category="unanswerable", answerable=False, q1=q1, q2=q2, boundary=bnd, severity="P0")

    # Misleading / conflicting (6 groups)
    misleading = [
        ("M01", "misleading", "月球仓库温度零下200度吗？", "调侃说月球仓库零下200度对不对？", "应澄清非正式政策"),
        ("M02", "conflicting_scope", "国内经济舱报销上限9999元吗？", "B库说报销9999是否适用于A库？", "必须不引用干扰库"),
        ("M03", "conflicting_scope", "凌云机柜 LY-0000 是否正确？", "机柜是不是 LY-0000？", "不得采用 B 库错误编号"),
        ("M04", "versioned_trap", "差旅经济舱上限是2800还是3100？", "最新经济舱上限？", "未指明版本则应说明或追问"),
        ("M05", "misleading", "韩方舟电话完整11位？", "夜班负责人全号？", "仅尾号5821可查"),
        ("M06", "conflicting_scope", "玄武备份是周一零点吗？", "备份窗口周一00:00？", "不得采用B库错误时间"),
    ]
    for i, (gid, cat, q1, q2, bnd) in enumerate(misleading):
        facts_m: list = []
        support_m: list = []
        ans = False
        if gid == "M04":
            ans = True
            facts_m = [["3100元", "3100"], ["2025"]]
            support_m = [{"document": "policy-v2025", "quote": "版本：2025-03。国内经济舱报销上限为3100元。"}]
        elif gid == "M05":
            ans = True
            facts_m = [["5821"]]
            support_m = [{"document": "ops-schedule", "quote": "夜班负责人：韩方舟；联系电话尾号5821。"}]
        add(
            gid,
            38 + i,
            category=cat,
            answerable=ans,
            q1=q1,
            q2=q2,
            facts=facts_m,
            support=support_m,
            boundary=bnd,
            severity="P0",
        )

    # Pad to >=100 distinct questions with additional factual paraphrases (14 groups)
    pad = [
        ("P01", "顾清岚负责哪个项目代号？", "AURORA-7 与谁关联？", [["AURORA-7"], ["顾清岚"]], [{"document": "multilingual-mix", "quote": "Project codename: AURORA-7. 项目负责人为顾清岚。"}], "factual"),
        ("P02", "检索候选池默认多少条？", "RETRIEVAL_POOL_K？", [["80"]], [{"document": "deep-tables", "quote": "| 检索池 | RETRIEVAL_POOL_K | 80条 | 候选池大小 |"}], "tables"),
        ("P03", "PO-8821 供应商中文名？", "北川精工对应哪张单？", [["北川精工"]], [{"document": "cross-doc-a", "quote": "订单号 PO-8821 的供应商为「北川精工」。"}], "cross_document"),
        ("P04", "PO-8821 签收人姓名？", "沈若谷出现在哪份回执？", [["沈若谷"]], [{"document": "cross-doc-b", "quote": "PO-8821 的到货签收人为「沈若谷」。"}], "cross_document"),
        ("P05", "SH-7712 用于哪栋楼？", "准入证编号所属楼宇？", [["霜华实验楼"]], [{"document": "scan-bulletin", "quote": "霜华实验楼的准入证编号为SH-7712。"}], "scan"),
        ("P06", "南巷堆场堆高上限？", "4.2米限制在哪？", [["4.2米"]], [{"document": "ocr-yard", "quote": "南巷堆场最大堆高为4.2米。"}], "ocr"),
        ("P07", "2024政策经济舱上限？", "2800元来自哪版？", [["2800"]], [{"document": "policy-v2024", "quote": "版本：2024-12。国内经济舱报销上限为2800元。"}], "versioned"),
        ("P08", "2025政策版本号？", "2025-03 版标识？", [["2025-03"]], [{"document": "policy-v2025", "quote": "版本：2025-03。国内经济舱报销上限为3100元。"}], "versioned"),
        ("P09", "缓存 TTL 120 秒对应模块？", "CACHE_TTL 属于哪层？", [["缓存层"]], [{"document": "deep-tables", "quote": "| 缓存层 | CACHE_TTL | 120秒 | 超时后需刷新 |"}], "tables"),
        ("P10", "OCR 页数上限模块名？", "OCR_PAGE_CAP 属于什么队列？", [["OCR队列", "OCR"]], [{"document": "deep-tables", "quote": "| OCR队列 | OCR_PAGE_CAP | 48页 | 单任务上限 |"}], "tables"),
        ("P11", "导出分批阈值？", "超过多少行需分批？", [["5000"]], [{"document": "deep-tables", "quote": "| 导出器 | EXPORT_ROW_LIMIT | 5000行 | 超出需分批 |"}], "tables"),
        ("P12", "学习卡批量上限？", "FLASHCARD_BATCH 数值？", [["25"]], [{"document": "deep-tables", "quote": "| 学习卡 | FLASHCARD_BATCH | 25张 | 单次生成上限 |"}], "tables"),
        ("P13", "CN-EAST-2 是什么类型的代码？", "故障切换区域？", [["CN-EAST-2"]], [{"document": "multilingual-mix", "quote": "Failover region code: CN-EAST-2. 故障切换区域代码为 CN-EAST-2。"}], "multilingual"),
        ("P14", "worker 并发六与英文 six 是否一致？", "six workers 上限？", [["6", "six"]], [{"document": "multilingual-mix", "quote": "Maximum concurrent ingest workers: 6 (six). 同时运行的采集 worker 不得超过6个。"}], "multilingual"),
    ]
    for i, (gid, q1, q2, ef, sup, cat) in enumerate(pad):
        add(gid, 44 + i, category=cat, answerable=True, q1=q1, q2=q2, facts=ef, support=sup, boundary="")

    return rows


def to_jsonl(facts_list: list[Fact]) -> list[dict]:
    cases: list[dict] = []
    for fact in facts_list:
        for i, question in enumerate(fact.questions, start=1):
            cases.append(
                {
                    "id": f"{fact.group}-{i}",
                    "fact_group": fact.group,
                    "split": fact.split,
                    "knowledge_base": fact.kb,
                    "question": question,
                    "answerable": fact.answerable,
                    "expected_facts": fact.expected_facts,
                    "support": fact.support,
                    "category": fact.category,
                    "severity": fact.severity,
                    "allowed_boundary": fact.allowed_boundary,
                    "review_status": REVIEW,
                }
            )
    return cases


def main() -> None:
    materials = build_materials()
    cases = to_jsonl(facts())
    assert len(cases) >= 100, len(cases)
    assert len({c["id"] for c in cases}) == len(cases)
    ROOT.joinpath("materials.json").write_text(
        json.dumps(materials, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    ROOT.joinpath("questions.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in cases) + "\n", encoding="utf-8"
    )
    # Cross-user API canaries (integration harness, not evaluate_rag schema)
    canaries = [
        {
            "id": "ISO-01",
            "area": "auth_isolation",
            "description": "用户A不得读取用户B知识库素材元数据",
            "severity": "P0",
            "review_status": REVIEW,
            "automated": "backend/tests/integration/test_material_api.py (pattern)",
        },
        {
            "id": "ISO-02",
            "area": "rag_isolation",
            "description": "用户A检索不得返回NIGHTJAR-3",
            "severity": "P0",
            "review_status": REVIEW,
            "automated": "MOCK: deep-test/harness/run_offline_gate.py --label integration",
        },
        {
            "id": "ISO-03",
            "area": "ssrf",
            "description": "模型连接测试仅允许 mock 本地目标",
            "severity": "P0",
            "review_status": REVIEW,
            "automated": "BLOCKED until dedicated SSRF mock server",
        },
    ]
    ROOT.joinpath("isolation_canaries.json").write_text(
        json.dumps(canaries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote {len(cases)} questions, {len(materials)} materials -> {ROOT}")


if __name__ == "__main__":
    main()