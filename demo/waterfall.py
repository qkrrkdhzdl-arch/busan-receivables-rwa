#!/usr/bin/env python3
"""발행 → 만기 상환 워터폴 계산 데모 (정합 모델).

가정(모두 데모용 가정값):
  채권 금액(face) 1억 원 · 만기 75일 · 유보금 10% · 투자자 연 5% · 발행·검증 비용 0.8%(발행액 기준)

현금 흐름 구조
  1) 발행: 투자자가 발행액(face - 유보금)을 납입 → 유통사 수령
  2) 만기: 회수액을 [비용 → 투자자 원금 → 투자자 이자 → 유통사 정산] 순서로 분배
     · 유보금은 발행액에서 떼어 둔 손실 흡수 버퍼이며, 비용·이자를 먼저 부담하고
       남은 금액이 유통사에게 반환된다.
  3) 회수액이 원금+이자에 못 미치면 그 부족분만큼 투자자 손실이 발생한다.
     즉 투자자 손실이 시작되는 연체율은 유보금 비율(10%)을 넘는 지점이다.

제안서 v2의 부록 A·5장 표는 이 모델과 같은 산식으로 맞춰 두었다(유보금에서 비용·이자를 먼저
부담하고 잔여분을 유통사에 반환, 투자자 손실은 유보금을 넘는 연체부터).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Terms:
    face: int = 100_000_000
    term_days: int = 75
    reserve_rate: float = 0.10
    yield_rate: float = 0.05
    cost_rate: float = 0.008

    @property
    def reserve(self) -> int:
        if hasattr(self, "_reserve"):
            return self._reserve
        return int(self.face * self.reserve_rate)

    @property
    def issue(self) -> int:
        return self.face - self.reserve

    @property
    def cost(self) -> int:
        return int(self.issue * self.cost_rate)

    @property
    def interest(self) -> int:
        return int(self.issue * self.yield_rate * self.term_days / 365)


def settle(terms: Terms, delayed_rate: float) -> dict:
    """연체율(delayed_rate)을 가정하고 만기 정산 결과를 계산한다."""
    collected = int(terms.face * (1 - delayed_rate))
    cost = min(terms.cost, collected)
    after_cost = collected - cost
    principal = min(terms.issue, after_cost)
    after_principal = after_cost - principal
    interest = min(terms.interest, after_principal)
    after_interest = after_principal - interest
    supplier_settlement = after_interest  # 유보금 잔여 + 초과 회수분
    investor_shortfall = (terms.issue + terms.interest) - (principal + interest)
    return {
        "delayed_rate": delayed_rate,
        "collected": collected,
        "cost": cost,
        "principal": principal,
        "interest": interest,
        "supplier_settlement": supplier_settlement,
        "investor_loss": max(0, investor_shortfall),
        "investor_loss_rate": max(0, investor_shortfall) / terms.issue,
        "supplier_total": terms.issue + supplier_settlement,
    }


def main() -> None:
    t = Terms()
    print("가정: 채권 1억 원 · 75일 · 유보금 10% · 투자자 연 5% · 비용 0.8%(발행액 기준)")
    print(f"발행액 {t.issue:,}원 · 유보금 {t.reserve:,}원 · 비용 {t.cost:,}원 · 투자자 이자 {t.interest:,}원")
    print()
    for name, rate in (("기준", 0.005), ("주의", 0.015), ("스트레스", 0.03), ("한계 너머", 0.12)):
        r = settle(t, rate)
        print(f"[{name}] 연체율 {rate:.1%} · 회수 {r['collected']:,}원")
        print(f"   비용 {r['cost']:,} → 투자자 원금 {r['principal']:,} → 이자 {r['interest']:,}"
              f" → 유통사 정산 {r['supplier_settlement']:,}")
        print(f"   투자자 손실 {r['investor_loss']:,}원 ({r['investor_loss_rate']:.2%}) · "
              f"유통사 총수령 {r['supplier_total']:,}원")
        print()


if __name__ == "__main__":
    main()
