"""生成试印报告（Markdown + JSON 摘要）。

报告分开回答：
1. 按实际发印安排会怎样（按原随机分组的意向效应）；
2. 如果纯粹换一版可能怎样（仅在可比条件下的实际接触比较）。
并给出续印核算、保护层覆盖与印单处置。
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from . import decision, effects
from .data import load_packet
from .observations import Frame, build_frame
from .stats import cell, worst_case_rd

LAYER_LABEL = {"minor": "少儿", "unfamiliar": "不熟悉场馆"}
HALL_LABEL = {"bronze": "bronze 厅", "clay": "clay 厅"}
REASON_LABEL = {
    "found": "窗口内独立找到",
    "found_late": "超过观测窗才找到",
    "assisted": "先求助",
    "not_found": "足窗未找到",
    "missing_result": "缺交班结果",
    "truncated": "窗口被截短",
    "after_close": "记录时间超过关厅",
    "cancelled": "关厅",
    "no_record": "有效邀请但无现场记录",
    "no_window": "没有可用窗口",
}


def _pct(value: float | None) -> str:
    return "—" if value is None or value != value else f"{value:.1%}"


def _pp(value: float | None) -> str:
    return "—" if value is None or value != value else f"{value:+.1%}"


def _ci(interval: tuple[float, float]) -> str:
    low, high = interval
    if low != low or high != high:
        return "—"
    return f"[{low:+.1%}, {high:+.1%}]"


def _ci_abs(interval: tuple[float, float]) -> str:
    low, high = interval
    if low != low or high != high:
        return "—"
    return f"[{low:.1%}, {high:.1%}]"


def _cell_text(value: cell) -> str:
    return f"{value.successes}/{value.known}（未知 {value.unknown}）"


def build_report(packet, frame: Frame) -> str:
    lines: list[str] = []
    add = lines.append

    itt = effects.itt_effect(frame)
    a_summary = effects.arm_summary(frame, "A")
    b_summary = effects.arm_summary(frame, "B")
    pairs = effects.pair_detail(frame)
    sens = effects.sensitivity(frame, packet)
    late = effects.late_receipt_impact(packet)
    treated = effects.as_treated(frame, packet)
    layers = effects.protected_layers(frame, packet)
    overlap = decision.layer_overlap(packet)
    selection = decision.select_plan(packet, frame)
    chosen = selection["best"]
    plan = chosen["plan"]
    orders = decision.order_actions(packet, plan)
    stops = decision.new_print_stop(packet, plan)
    supply, signed, committed = decision.stock_and_orders(packet)
    worst = worst_case_rd(
        [row for row in frame.observations if row.assigned == "A"],
        [row for row in frame.observations if row.assigned == "B"],
    )

    total = len(frame.observations)
    known = sum(row.known for row in frame.observations)
    unknown = total - known
    contaminated = sum(row.contaminated for row in frame.observations)
    no_exposure = sum(row.no_exposure for row in frame.observations)
    b_contam = [row for row in frame.observations if row.assigned == "B" and row.contaminated]
    b_contam_success = sum(row.outcome == "success" for row in b_contam)
    pure_batch1_a = sum(
        1 for row in build_frame(packet, loan_batch=1).observations if row.pure and row.assigned == "A"
    )
    pure_batch1_b = sum(
        1 for row in build_frame(packet, loan_batch=1).observations if row.pure and row.assigned == "B"
    )
    pure_batch2_a = sum(1 for row in frame.observations if row.pure and row.assigned == "A")
    pure_batch2_b = sum(1 for row in frame.observations if row.pure and row.assigned == "B")

    add("# 展厅试印报告：旧版 A 与新版 B")
    add("")
    add(
        "本报告基于离线合成记录。观测单位是「一个家庭在一场内的首次有效找寻机会」；"
        "同一家庭跨场各算一场，重复补记不另算访客。"
    )
    add("")

    # ------------------------------------------------------------------ 0
    add("## 0. 记录口径与取舍")
    add("")
    add(
        f"- 观测总数 {total} 条家庭×场次；重复补记行 {frame.duplicate_visit_rows} 条，只计首次有效机会。"
    )
    add(
        f"- 未实到邀请 {len(frame.excluded_not_entered)} 条，直接排除，不作为找错："
        + ("、".join(row["household"] for row in frame.excluded_not_entered) or "无")
        + "。"
    )
    add(
        f"- 关厅场次：{'、'.join(frame.cancelled_sessions) or '无'}；"
        "关厅、窗口不足造成的提前截尾一律记为未知。"
    )
    add(
        "- 成功＝窗口内独立找到；失败＝先求助、或足窗仍未找到；未知＝关厅、截尾、"
        "缺交班结果、有效邀请但无现场记录。未知不计零，也不从分母里删掉。"
    )
    add(
        "- 串版识别同时使用现场记录与借阅补记，并纳入第 2 批晚到借阅单；"
        "户级跨场接触单独保留。"
    )
    add(
        "- 「按实际发印安排」用原随机分组（意向效应）；"
        "「纯粹换一版」只用实际接触且可比的观测。两问不混用数据。"
    )
    add("")

    # ------------------------------------------------------------------ 1
    add("## 1. 问题一：按实际发印安排的随机分组效应")
    add("")
    add("按**原随机分组**（不是按实际拿到的版）比较，未知保留在分母里。")
    add("")
    add("| 分配版 | 观测 | 已知成功/已知 | 成功率 | Wilson 95% | 未知 | 未知全失败–全成功 |")
    add("| --- | ---: | ---: | ---: | --- | ---: | --- |")
    for summary, label in ((a_summary, "A（旧）"), (b_summary, "B（新）")):
        add(
            f"| {label} | {summary.assigned.n} | {_cell_text(summary.assigned)} | "
            f"{_pct(summary.assigned.rate)} | {_ci_abs(summary.assigned_ci)} | "
            f"{summary.assigned.unknown} | {_pct(summary.known_bounds[0])}–{_pct(summary.known_bounds[1])} |"
        )
    add("")
    add(
        f"**主估计（按场次对分层的比例差，B − A）：{_pp(itt['estimate'])}；"
        f"按户聚类 95% 区间 {_ci(itt['interval'])}；"
        f"配对随机化精确检验 p = {itt['permutation_p']:.3f}（{itt['n_pairs']} 对）。**"
    )
    add("")
    add(
        f"缺测的最坏情形：A 成功率可达 {_pct(worst['a_bounds'][0])}–{_pct(worst['a_bounds'][1])}，"
        f"B 可达 {_pct(worst['b_bounds'][0])}–{_pct(worst['b_bounds'][1])}，"
        f"比例差可达 {_pp(worst['rd_min'])} 至 {_pp(worst['rd_max'])}。"
    )
    add("")
    add(
        "读法：现有样本既不能证明新版更好，也不能排除新版造成超过经营损失上限的退步。"
        "区间跨零，且下沿远低于各保护层允许的最大损失。"
    )
    add("")
    add("### 1.1 各场次对明细")
    add("")
    add("| 对 | 馆 | 志愿者 | A 成功/已知 | B 成功/已知 | B−A | A 未知 | B 未知 |")
    add("| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |")
    for row in pairs:
        add(
            f"| {row['pair']} | {row['hall']} | {row['volunteer']} | {_cell_text(row['a'])} | "
            f"{_cell_text(row['b'])} | {_pp(row['diff'])} | {row['a'].unknown} | {row['b'].unknown} |"
        )
    add("")
    p2_a = [row for row in frame.observations if row.pair == "P2" and row.assigned == "A"]
    add(
        "P2 的 A 场（S2B）实开仅 10 分钟，低于 20 分钟观测窗，"
        f"A 侧 {len(p2_a)} 条里 {sum(not row.known for row in p2_a)} 条为未知；"
        "P6 的 A 场（S6B）关厅，A 侧没有任何观测。两对都不能按同一条件比较。"
    )
    add("")

    # ------------------------------------------------------------------ 2
    add("## 2. 缺测与串版各占多少分量")
    add("")
    add(
        f"全部 {total} 条观测中，已知 {known} 条、未知 {unknown} 条"
        f"（{unknown / total:.1%}）；有串版接触 {contaminated} 条；"
        f"完全没有接触记录 {no_exposure} 条。"
    )
    add("")
    add("| 未知原因 | A 臂 | B 臂 |")
    add("| --- | ---: | ---: |")
    reasons_a = Counter(row.reason for row in frame.observations if row.assigned == "A")
    reasons_b = Counter(row.reason for row in frame.observations if row.assigned == "B")
    for reason in ("cancelled", "truncated", "after_close", "no_window", "missing_result", "no_record"):
        if reasons_a.get(reason, 0) or reasons_b.get(reason, 0):
            add(f"| {REASON_LABEL[reason]} | {reasons_a.get(reason, 0)} | {reasons_b.get(reason, 0)} |")
    add("")
    add("改变口径后的估计（每一行都重新分层，不是只换分母）：")
    add("")
    add("| 口径 | n | 串版 | 未知 | B−A（按对分层） | B−A 区间（两比例） |")
    add("| --- | ---: | ---: | ---: | ---: | --- |")
    for row in sens:
        add(
            f"| {row['label']} | {row['n']} | {row['contaminated']} | {row['unknown']} | "
            f"{_pp(row['estimate'])} | {_ci(row['diff_ci'][1:])} |"
        )
    add("")
    add(
        f"要点：B 臂里 {len(b_contam)} 条「接触过非本场分配版次」的观测中有 "
        f"{b_contam_success} 条成功。只保留真正只接触本场分配版的观测时，方向翻为负"
        f"（{_pp(late[2]['pure_effect'])}）。串版不是无关噪声，而是与结果同向的选择性偏差，"
        "不能把主口径的正号当作新版有效的证据。"
    )
    add("")
    add("### 2.1 晚到借阅单的影响")
    add("")
    add("| 纳入批次 | 串版观测 | 无接触记录 | 意向效应（全部） | 纯版比较效应 | 纯版样本 A/B |")
    add("| --- | ---: | ---: | ---: | ---: | --- |")
    for batch in (1, 2):
        row = late[batch]
        add(
            f"| 第 {batch} 批 | {row['contaminated']} | {row['no_exposure']} | "
            f"{_pp(row['effect'])} | {_pp(row['pure_effect'])} | "
            f"{row['pure_a'].n}/{row['pure_b'].n} |"
        )
    add("")
    add(
        f"第 2 批借阅单把 H-shared/S1B 与 H2B1/S2B 补成跨版接触，"
        f"纯版样本从 {pure_batch1_a}/{pure_batch1_b} 变为 {pure_batch2_a}/{pure_batch2_b}，"
        f"纯版比较由 {_pp(late[1]['pure_effect'])} 变为 {_pp(late[2]['pure_effect'])}。"
        "这可以改变「能否据纯版比较续印」的判断，但不改先前的随机分组："
        "H-shared/S1B 仍按 B 臂计入意向效应。"
    )
    add("")

    # ------------------------------------------------------------------ 3
    add("## 3. 问题二：实际接触下的可比比较")
    add("")
    add(
        "只在**重叠可比**时单独说明：两场都有完整观测窗、且家庭只接触单一版次。"
        "它回答「纯粹换一版可能怎样」，但接触哪一版不是随机分配的，不能当作随机效果。"
    )
    add("")
    excluded = "、".join(
        f"{pair}（{'；'.join(reasons)}）" for pair, reasons in treated["excluded_reasons"].items()
    )
    add(f"- 排除的场次对：{excluded or '无'}。")
    add(f"- 进入比较：{treated['n']} 条观测、{treated['households']} 个家庭。")
    add(
        f"- 按对分层 B−A：{_pp(treated['estimate'])}；按户聚类 95% 区间 {_ci(treated['interval'])}。"
    )
    add("")
    add("| 对 | 志愿者 | A 实际 | B 实际 | B−A | 重叠（A/B） |")
    add("| --- | --- | ---: | ---: | ---: | --- |")
    for row in treated["detail"]:
        add(
            f"| {row['pair']} | {row['volunteer']} | {_cell_text(row['a'])} | {_cell_text(row['b'])} | "
            f"{_pp(row['diff'])} | {row['overlap'][0]}/{row['overlap'][1]} |"
        )
    add("")
    add("同一志愿者跨厅协助，必须分开说：")
    add("")
    add("| 志愿者 | A 实际 | B 实际 | B−A 区间 |")
    add("| --- | ---: | ---: | --- |")
    for volunteer, row in treated["by_volunteer"].items():
        add(
            f"| {volunteer} | {_cell_text(row['a'])} | {_cell_text(row['b'])} | "
            f"{_ci(row['diff_ci'][1:])} |"
        )
    add("")
    add(
        "实际接触比较的点估计为负，但区间跨零；且两版的实际接触者按户、按志愿者都不平衡。"
        "它只能说明「没有看到新版更好的迹象」，不能反过来证明新版一定更差。"
    )
    add("")

    # ------------------------------------------------------------------ 4
    add("## 4. 受保护访客：分层证据")
    add("")
    add(
        "少儿与不熟悉场馆分别受保护，一个家庭可同时属于两层。下表按**预约时的分层标识**统计，"
        "不用现场 visitor_profile 代替，也不把全厅合并区间拿来替某一层盖章。"
    )
    add("")
    invited = {row["household"] for row in packet.invitations if row["entered"] == "yes"}
    labelled = {row["household"] for row in packet.invitations if row["prereg_profile"]}
    add(
        f"**覆盖限制：**邀请表只有 {len(invited)} 个有效到访家庭带预约分层（{len(labelled)} 户有标签），"
        f"而全场观测有 {len({row.household for row in frame.observations})} 个家庭。"
        "因此保护层只能在这少数家庭上观察，其余家庭无法补标签，也不能假设他们不属于某一层。"
    )
    add("")
    add("| 层 | A 分配 | B 分配 | A 成功率 | B 成功率 | B−A 区间 | 两层兼有 |")
    add("| --- | ---: | ---: | ---: | ---: | --- | --- |")
    for name in ("minor", "unfamiliar"):
        row = layers[name]
        add(
            f"| {LAYER_LABEL[name]} | {_cell_text(row['a'])} | {_cell_text(row['b'])} | "
            f"{_pct(row['a'].rate)} | {_pct(row['b'].rate)} | {_ci(row['diff_ci'][1:])} | "
            f"{len(row['both_layers'])} |"
        )
    add("")
    add(
        "两层的已知结果都只有个位数；少儿层看似给出正下沿，是因为 B 臂 2 条全部成功，"
        "属于小样本假象，不是可续印的证据。"
        f"按每臂至少 {decision.MIN_KNOWN_PER_ARM} 条已知结果的证据门槛，两层都不够；"
        "**不能据此把新版发给任一层，也不能用全厅结果替代。**"
    )
    add("")
    add("续印需求的分层人数（直接相加会重复计重叠家庭）：")
    add("")
    add("| 场次 | 少儿 | 不熟悉 | 两层兼有 | 直接相加 | 去重并集 | 其它 | 合计 |")
    add("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for option_name, row in overlap.items():
        add(
            f"| {option_name} | {row['minor']} | {row['unfamiliar']} | {row['both']} | "
            f"{row['naive_sum']} | {row['union']} | {row['other']} | {row['total']} |"
        )
    add("")
    add(
        "印数按去重并集计，不按两层相加："
        + "；".join(
            f"{option_name} 重复计 {row['double_counted']} 户"
            for option_name, row in overlap.items()
            if row["double_counted"]
        )
        + "。"
    )
    add("")

    # ------------------------------------------------------------------ 5
    add("## 5. 印务与经营约束")
    add("")
    add("| 版 | 语种 | 现货 | 整批起印 | 锁期（日） | 单位成本（分） |")
    add("| --- | --- | ---: | ---: | ---: | ---: |")
    for (edition, language), row in sorted(packet.inventory.items()):
        add(
            f"| {edition} | {language} | {row['stock']} | {row['pack_size']} | "
            f"{row['lock_day']} | {row['unit_cost']} |"
        )
    add("")
    add(
        "已签印量（state=approved）："
        + (
            "、".join(f"{edition}/{language} {quantity}" for (edition, language), quantity in sorted(signed.items()))
            or "无"
        )
        + f"，对应已承诺成本 {committed} 分。notified 只是对外发放承诺，不计入已签印量。"
    )
    add("")
    add(
        "可供量（现货 + 已签）："
        + "、".join(
            f"{edition}/{language} {quantity}" for (edition, language), quantity in sorted(supply.items())
        )
        + "。"
    )
    add("")
    add(
        "**相邻与 flush：**F1 固定 A，且 F1 结束即 F2 开始、间隔 0 分钟，小于 flush 10 分钟，"
        "因此 F2 也必须是 A；F3、F4 可选。"
    )
    if chosen["adjacency"]:
        for row in chosen["adjacency"]:
            add(
                f"- 冲突：{row['hall']} 第 {row['day']} 天 {row['left']}→{row['right']} "
                f"异版且间隔 {row['gap']} 分钟 < 要求 {row['required']} 分钟。"
            )
    else:
        add("- 所选安排没有「相邻异版且间隔不足」的冲突。")
    add("")
    add(
        "**合用服务台：**F1–F3 共用 D-x，F4 用 D-y。在 D-x 上新旧兼发会让相邻场次出现"
        "与试印不同的接触组合；F4 在 D-y 单独发新版时，同台没有旧版。"
    )
    add("")
    add(
        "**无需求行的场次：**F5–F8 在排期表里，但 `dispatch_profiles.tsv` 没有它们的需求行，"
        "**不能凭库存替它们核定印数**；clay 厅的护栏只能作证据参考，不能生成续印量。"
    )
    add("")

    # ------------------------------------------------------------------ 6
    add("## 6. 候选发放安排")
    add("")
    add(
        "| F3 | F4 | 追加整批成本（分） | 已签成本（分） | 超承诺新版户数 | 承诺冲突 | 保护层问题 | 分组护栏问题 |"
    )
    add("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for result in selection["candidates"]:
        result_plan = result["plan"]
        f4 = result_plan.assignment["F4"]
        f4_text = "+".join(f"{edition}×{count}" for edition, count in sorted(f4.items()))
        add(
            f"| {result_plan.primary('F3')} | {f4_text} | {result['purchase']['new_cost']} | "
            f"{result['purchase']['committed_cost']} | {result['uncommitted_new']} | "
            f"{len(result['commitments'])} | {len(result['layer_problems'])} | "
            f"{len(result['group_problems'])} |"
        )
    add("")
    add(
        "没有任何候选同时满足「兑现已通知承诺」「不超追加预算」「保护层证据达标且不越混用上限」。"
        "缺的是分层证据，不是印数。"
    )
    add("")

    # ------------------------------------------------------------------ 7
    add("## 7. 建议的下一步发放与续印")
    add("")
    add("建议的**最低限度**安排：只兑现既有承诺，不扩大新版。")
    add("")
    add("| 场次 | 拟发版次与户数 | 受保护访客可能接触 | 说明 |")
    add("| --- | --- | --- | --- |")
    for row in chosen["coverage"]:
        split = plan.assignment.get(row.option, {})
        split_text = " + ".join(f"{edition}×{count}" for edition, count in sorted(split.items()))
        add(f"| {row.option} | {split_text} | {'、'.join(row.may_contact) or '—'} | {row.reason} |")
    add("")
    add(
        f"- 追加整批成本 {chosen['purchase']['new_cost']} 分，追加预算 {chosen['budget']} 分，"
        f"{'未超' if not chosen['over_budget'] else '已超'}；"
        f"已签成本 {chosen['purchase']['committed_cost']} 分照记，不能因为不用而抹掉；"
        f"整批浪费 {chosen['purchase']['waste']} 册。"
    )
    add("")
    add("**关键判断：不据现有证据扩大续印新版。**")
    add("")
    add(
        "- 已有证据能保证的范围：继续发旧版 A 的场次（F1、F2、F3）保持旧版，这些场次的保护层不需要新版承诺。"
    )
    add(
        "- 超出支持、须保留旧版的范围：F4 的 12 户里，已通知的 6 户必须拿到 B（P-o3，不能撤），"
        "其余 6 户没有新版依据，应保留 A；这形成同场混用，需要单独披露。"
    )
    bronze_rows = [
        row
        for key, row in chosen["guardrails"]["layers"].items()
        if key == ("bronze", "unfamiliar")
    ]
    if bronze_rows:
        row = bronze_rows[0]
        add(
            f"- bronze 不熟悉场馆层的混用占比在 {row.mixed_share_lower:.1%}–{row.mixed_share:.1%} 之间，"
            f"最不利情形高于上限 {row.mixed_ceiling:.0%}；这是既有通知造成的、无法单方撤回的越界，"
            "应按最不利准备并升级签字确认，而不是用全厅区间掩盖。"
        )
    add("")

    # ------------------------------------------------------------------ 8
    add("### 7.1 印单处置")
    add("")
    add("| 印单 | 场次 | 版/语种 | 数量 | 状态 | 处置 | 去向 | 锁期 | 说明 |")
    add("| --- | --- | --- | ---: | --- | --- | --- | ---: | --- |")
    for action in orders:
        add(
            f"| {action.order} | {action.option} | {action.edition}/{action.language} | "
            f"{action.qty} | {action.state} | {action.disposition} | {action.destination} | "
            f"{action.lock_day} | {action.note} |"
        )
    add("")
    if stops:
        add("未签印数应停下的追加整批：")
        add("")
        for row in stops:
            add(
                f"- {row['key'][0]}/{row['key'][1]}：缺口 {row['shortfall']} 册，整批 {row['pack_size']} 册，"
                f"费用 {row['cost']} 分，锁期第 {row['lock_day']} 天；{row['reason']}。"
            )
    else:
        add(
            "按所选安排不需要新签任何整批：B/en 现货 7 册覆盖已通知的 6 户，"
            "A/en 现货 25 册覆盖所选安排的 20 户，A/zh 与 B/zh 也有余量。"
            "后续若人数上升，最低起印量超过仍有依据的人数时允许不追加；"
            "但已通知的承诺不能撤，已发生的浪费不能从成本里减掉。"
        )
    add("")
    add("### 7.2 补交借阅单后要查的三件事")
    add("")
    add("1. **存量仍可发**：A/zh、A/en、B/en 的现货与已签量覆盖所选安排；B/zh 现货 18 册加已签 40 册暂闲置。")
    add(
        "2. **已下单的册改有依据的去向**：P-o2（F2/B/zh 40 册，已签）在原场没有新版依据，"
        "同版同语种也没有通过保护层的场次，只能记为闲置；其成本保留在账上。"
    )
    add("3. **未签印数应停**：不新签 B/zh 或 B/en 整批；若最低起印量超过后来仍有依据的人数，允许不追加。")
    add("")
    add(
        "下一轮仍从这个项目接收试印、印单与实发记录：历史签发（P-o1、P-o2、P-o3）与当前证据下的待办分开列示；"
        "补录借阅单只更新接触证据与待办，不回改历史签发，也不改先前的随机分组。"
    )
    add("")

    # ------------------------------------------------------------------ 9
    add("## 8. 什么证据才足以续印新版")
    add("")
    add("要在下月继续发新版，至少需要同时满足：")
    add("")
    add(
        f"1. 按原随机分组的意向效应下沿不低于各层最低增益，且上沿损失不超过最大损失；"
        f"当前下沿为 {_pp(itt['interval'][0])}，不满足。"
    )
    add(
        "2. 少儿与不熟悉场馆两层各自有足够已知结果，且都在同一可比条件下达标；"
        "当前两层已知结果都只有个位数，不能用全厅区间替任一层盖章。"
    )
    add("3. 纯版比较与意向效应方向一致；当前二者相反，串版与选择偏差尚未排除。")
    add(
        "4. 拟发安排在库存、起印整批、锁期、混用上限与预算内可执行；"
        "当前唯一兑现承诺的混用方案，在不熟悉层的最不利混用情形已越过上限。"
    )
    add("")
    add(
        "在这些条件满足前，建议继续发旧版、只兑现已通知的新版承诺，"
        "并把 P-o2 的沉没成本与 F4 的混用越界如实列入经营记录。"
    )
    add("")
    return "\n".join(lines)


def summary_json(packet, frame: Frame) -> dict:
    itt = effects.itt_effect(frame)
    treated = effects.as_treated(frame, packet)
    late = effects.late_receipt_impact(packet)
    layers = effects.protected_layers(frame, packet)
    selection = decision.select_plan(packet, frame)
    chosen = selection["best"]

    def arm(edition: str) -> dict:
        value = effects.arm_summary(frame, edition).assigned
        return {"n": value.n, "known": value.known, "successes": value.successes, "unknown": value.unknown}

    return {
        "observations": len(frame.observations),
        "duplicate_rows": frame.duplicate_visit_rows,
        "excluded_not_entered": [row["household"] for row in frame.excluded_not_entered],
        "cancelled_sessions": frame.cancelled_sessions,
        "itt": {
            "a": arm("A"),
            "b": arm("B"),
            "estimate": itt["estimate"],
            "interval": list(itt["interval"]),
            "permutation_p": itt["permutation_p"],
        },
        "as_treated": {
            "estimate": treated["estimate"],
            "interval": list(treated["interval"]),
            "excluded_pairs": treated["excluded_pairs"],
            "n": treated["n"],
        },
        "late_receipt": {str(batch): row for batch, row in late.items()},
        "protected_layers": {
            name: {
                "a": {"n": row["a"].n, "known": row["a"].known, "successes": row["a"].successes},
                "b": {"n": row["b"].n, "known": row["b"].known, "successes": row["b"].successes},
                "diff": row["diff_ci"][0],
                "interval": list(row["diff_ci"][1:]),
            }
            for name, row in layers.items()
        },
        "chosen_plan": {option: split for option, split in chosen["plan"].assignment.items()},
        "chosen": {
            "new_cost": chosen["purchase"]["new_cost"],
            "committed_cost": chosen["purchase"]["committed_cost"],
            "budget": chosen["budget"],
            "over_budget": chosen["over_budget"],
            "commitments": chosen["commitments"],
            "layer_problems": {
                "/".join(key) if isinstance(key, tuple) else str(key): value
                for key, value in chosen["layer_problems"].items()
            },
        },
        "orders": [
            {
                "order": action.order,
                "disposition": action.disposition,
                "destination": action.destination,
                "note": action.note,
            }
            for action in decision.order_actions(packet, chosen["plan"])
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成展厅试印报告")
    parser.add_argument("folder", type=Path, nargs="?", default=Path("data"))
    parser.add_argument("--markdown", type=Path, default=Path("report.md"))
    parser.add_argument("--json", type=Path, default=Path("report.json"))
    arguments = parser.parse_args(argv)
    packet = load_packet(arguments.folder)
    frame = build_frame(packet)
    markdown = build_report(packet, frame)
    arguments.markdown.write_text(markdown, encoding="utf-8")
    arguments.json.write_text(
        json.dumps(summary_json(packet, frame), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"已写入 {arguments.markdown} 与 {arguments.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
