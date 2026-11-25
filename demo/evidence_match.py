#!/usr/bin/env python3
"""4종 증빙 대조로 외상매출채권 '확정' 여부를 판정하는 데모.

입력: 주문서(order) · 출고 전표(shipment) · 검수 확인(inspection) · 세금계산서(invoice)
      + 발행 이력(registry) · 채무자 통지/승낙 상태
출력: 건별 확정(PASS) / 보류(HOLD) 판정과 사유, 채권 해시

실제 제안(부산 수산물 납품확정 채권 RWA)의 1단계 '확정' 로직을 해커톤 데모 범위로
축소한 구현입니다. 문서 OCR·AI 판독은 범위 밖이며, 추출이 끝난 구조화 데이터를
검증 로직에 넣는 부분만 다룹니다.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

REQUIRED_DOCS = ("order", "shipment", "inspection", "invoice")


@dataclass
class Check:
    item: str
    ok: bool
    detail: str


@dataclass
class Verdict:
    case_id: str
    status: str = "PASS"
    checks: list[Check] = field(default_factory=list)
    digest: str = ""

    def add(self, item: str, ok: bool, detail: str) -> None:
        self.checks.append(Check(item, ok, detail))
        if not ok:
            self.status = "HOLD"

    @property
    def reasons(self) -> list[str]:
        return [f"{c.item}: {c.detail}" for c in self.checks if not c.ok]


def _d(value: str) -> date:
    return date.fromisoformat(value)


def _canonical(case: dict) -> str:
    """채권 해시: 거래처 상호·개인정보 대신 계약 식별 필드만 정규화."""
    payload = {
        "buyer_biz_no": case["invoice"]["buyer_biz_no"],
        "total": case["invoice"]["total"],
        "issue_date": case["invoice"]["issue_date"],
        "due_date": case["invoice"]["due_date"],
        "lot": case["shipment"].get("lot", ""),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    return "0x" + hashlib.sha256(blob).hexdigest()


def verify(case: dict, registry: dict) -> Verdict:
    case_id = case.get("case_id", "?")
    verdict = Verdict(case_id)
    missing = [d for d in REQUIRED_DOCS if d not in case]
    if missing:
        verdict.add("증빙 완비", False, f"누락 문서: {', '.join(missing)}")
        return verdict
    order, ship, insp, inv = (case[k] for k in REQUIRED_DOCS)

    # 1) 거래처 사업자번호 일치 (주문서 = 세금계산서)
    verdict.add(
        "거래처 사업자번호",
        order["buyer_biz_no"] == inv["buyer_biz_no"],
        f"{order['buyer_biz_no']} vs {inv['buyer_biz_no']}",
    )

    # 2) 품목 일치 (출고 전표 = 세금계산서)
    verdict.add(
        "품목 일치",
        ship["item"] == inv["item"],
        f"{ship['item']} vs {inv['item']}",
    )

    # 3) 수량 일치 — 검수 수량이 기준 (불일치 시 사유 기재 필수)
    qty_ok = insp["qty"] == inv["qty"] and ship["qty"] == insp["qty"]
    detail = f"검수 {insp['qty']} / 세금계산서 {inv['qty']} / 출고 {ship['qty']}"
    if not qty_ok and insp.get("qty_note"):
        detail += f" (사유: {insp['qty_note']})"
        qty_ok = insp.get("qty_note_confirmed", False)
    verdict.add("수량 일치", qty_ok, detail)

    # 4) 금액 정합성: 수량 × 단가 = 합계
    order_math = order["qty"] * order["unit_price"] == order["total"]
    inv_math = inv["qty"] * inv["unit_price"] == inv["total"]
    verdict.add("금액 계산", order_math and inv_math, f"주문 {order['total']} / 세금계산서 {inv['total']}")

    # 5) 발행일 순서: 검수일 ≤ 세금계산서 발행일
    verdict.add(
        "발행일 순서",
        _d(insp["inspected_date"]) <= _d(inv["issue_date"]),
        f"검수 {insp['inspected_date']} / 발행 {inv['issue_date']}",
    )

    # 6) 만기 구간 60~90일
    term_days = (_d(inv["due_date"]) - _d(inv["issue_date"])).days
    verdict.add("만기 60~90일", 60 <= term_days <= 90, f"{term_days}일")

    # 7) 이중 발행 차단: 같은 채권 해시가 발행 이력에 없어야 함
    digest = _canonical(case)
    verdict.digest = digest
    verdict.add(
        "이중 발행",
        digest not in registry.get("issued", []),
        "발행 이력 없음" if digest not in registry.get("issued", []) else "동일 채권 해시 발행 이력 있음",
    )

    # 8) 채무자 양도 통지·승낙 (민법 제450조)
    verdict.add("양도 통지", bool(case.get("transfer_notice_sent")), "통지 발송 여부")
    verdict.add("채무자 승낙", bool(case.get("debtor_accepted")), "승낙 회수 여부")

    # 9) 분리계좌 수납위임
    verdict.add("분리계좌 수납위임", bool(case.get("segregated_account_contract")), "계약 체결 여부")

    # 10) 제외 대상: 특수관계 거래·위탁판매
    verdict.add("특수관계 거래 아님", not case.get("related_party", False), "대표·임원 관계 조회")
    verdict.add("위탁판매 아님", not case.get("consignment", False), "반품 조건부 거래 아님")

    return verdict


def load_cases(paths: list[Path]) -> list[dict]:
    cases = []
    for p in paths:
        with p.open(encoding="utf-8") as f:
            cases.append(json.load(f))
    return cases


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parent
    sample_dir = root / "samples"
    registry_path = sample_dir / "registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8")) if registry_path.exists() else {"issued": []}

    paths = [Path(a) for a in argv[1:]] or sorted(sample_dir.glob("case-*.json"))
    if not paths:
        print("검사할 케이스가 없습니다. demo/samples/case-*.json을 확인하세요.")
        return 2

    cases = load_cases(paths)
    passed = 0
    print(f"{'케이스':<10}{'판정':<8}채권 해시")
    print("-" * 78)
    for case in cases:
        verdict = verify(case, registry)
        print(f"{verdict.case_id:<10}{verdict.status:<8}{verdict.digest[:26]}…")
        if verdict.status == "PASS":
            passed += 1
        for reason in verdict.reasons:
            print(f"    └ {reason}")
    print("-" * 78)
    print(f"확정 {passed}건 / 보류 {len(cases) - passed}건 (총 {len(cases)}건)")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
