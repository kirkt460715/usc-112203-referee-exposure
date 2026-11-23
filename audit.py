#!/usr/bin/env python3
"""跨赛区裁判培训的资格证据核对。

读取 records/ 下的离线业务记录，回答四件事：

1. 原案、派生试题与课堂讲评的对应关系（含实际出席范围）。
2. 每名学员按岗位保留的答卷证据：覆盖原案数、跨卷可比共同题、正确率。
3. 已发出资格通知在“当时资料时点”和“当前台账”下分别是否站得住。
4. 下一轮考核是否还能组成合规且两区可比的试卷；缺哪类题、最少要启用哪道备题。

数据口径：

* score 为单题失误分：0 = 判定正确，1 = 判定错误；min_score 是最低正确率。
  （两份已发通知的持有人 R31、R2 答卷全为 0，且在签发时点均达到分数线，
  说明业务记录里的 0 代表正确。）
* 同一原案同一岗位只算一项独立证据（distinct_cases 与锚题数都按原案去重）。
* 个人答卷受揭示影响，当且仅当：揭示已实际发生、该学员在出席名单内、
  揭示事实与题目 item_fact 相同、且揭示时间早于本卷 submit。
* 共同题的“跨卷可比”按题目整体判断：该题依赖的事实只要在任何赛区课堂被揭示，
  该题就不能再充当两区试卷之间的锚题（未到场学员自己的答卷仍可计分）。
* 未来命题使用 actual or scheduled：已排期但未举行的讲评也会烧掉相应题目。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations
from pathlib import Path
from typing import Sequence

SCORE_CORRECT = 0
DEFAULT_RECORDS = Path(__file__).resolve().parent / "records"


def _ts(value):
    return datetime.fromisoformat(value) if value else None


@dataclass(frozen=True)
class Item:
    id: str
    case: str
    fact: str
    role: str
    form: str
    season: int
    active: bool

    @property
    def common(self):
        return self.form == "common"


@dataclass(frozen=True)
class Answer:
    id: str
    person: str
    item: str
    score: int
    submit: datetime
    cycle: int
    form: str


@dataclass(frozen=True)
class Disclosure:
    id: str
    case: str
    fact: str
    attendees: frozenset
    scheduled: datetime
    actual: object
    received: datetime

    @property
    def held(self):
        return self.actual is not None

    def held_at(self):
        """课堂实际开始时刻；未举行则为 None。"""
        return self.actual

    def planned_at(self):
        """未来命题使用的时刻：已举行用实际，未举行用排期。"""
        return self.actual if self.actual is not None else self.scheduled


@dataclass(frozen=True)
class Applicant:
    id: str
    region: str
    roles: tuple
    course: str


@dataclass(frozen=True)
class Policy:
    role: str
    distinct_cases: int
    anchors: int
    min_score: float


@dataclass(frozen=True)
class Form:
    id: str
    region: str
    size: int
    sit: datetime
    announce_before: datetime
    cycle: int


@dataclass(frozen=True)
class Notice:
    id: str
    person: str
    roles: tuple
    as_of: datetime
    issued: datetime


class Records:
    """records/ 的只读视图，可用 catalog.json 校验。"""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.catalog = json.loads((self.root / "catalog.json").read_bytes())
        raw = {}
        for name in self.catalog:
            raw[name] = (self.root / (name + ".jsonl")).read_bytes()
        self.raw = raw
        rows = {name: [json.loads(line) for line in data.splitlines()]
                for name, data in raw.items()}

        self.items = {r["id"]: Item(r["id"], r["case"], r["item_fact"],
                                    r["role"], r["form"], r["season"],
                                    r["active"]) for r in rows["items"]}
        self.answers = [Answer(r["id"], r["person"], r["item"], r["score"],
                               _ts(r["submit"]), r["cycle"], r["form"])
                        for r in rows["answers"]]
        self.disclosures = [Disclosure(r["id"], r["case"], r["fact"],
                                       frozenset(r["attendees"]),
                                       _ts(r["scheduled"]), _ts(r["actual"]),
                                       _ts(r["received"]))
                            for r in rows["disclosures"]]
        self.applicants = {r["id"]: Applicant(r["id"], r["region"],
                                              tuple(r["roles"]), r["course"])
                           for r in rows["applicants"]}
        self.policy = {r["role"]: Policy(r["role"], r["distinct_cases"],
                                         r["anchors"], r["min_score"])
                       for r in rows["policy"]}
        self.forms = [Form(r["id"], r["region"], r["size"], _ts(r["sit"]),
                           _ts(r["announce_before"]), r["cycle"])
                      for r in rows["forms"]]
        self.notices = [Notice(r["id"], r["person"], tuple(r["roles"]),
                               _ts(r["as_of"]), _ts(r["issued"]))
                        for r in rows["notices"]]
        self.answers_by_person = defaultdict(list)
        for a in self.answers:
            self.answers_by_person[a.person].append(a)

    def verify(self):
        """按 catalog 校验行数与 sha256；返回问题清单。"""
        problems = []
        for name, expected in self.catalog.items():
            data = self.raw[name]
            if hashlib.sha256(data).hexdigest() != expected["sha256"]:
                problems.append("%s: sha256 不符" % name)
            rows = [json.loads(line) for line in data.splitlines()]
            if len(rows) != expected["rows"]:
                problems.append("%s: 行数 %d != %d" % (name, len(rows),
                                                      expected["rows"]))
            if len({r["id"] for r in rows}) != len(rows):
                problems.append("%s: id 重复" % name)
        return problems


# ---------------------------------------------------------------------------
# 证据计算
# ---------------------------------------------------------------------------

@dataclass
class RoleEvidence:
    person: str
    role: str
    kept: list            # [(Answer, Item)]
    removed: list         # [(Answer, Item, Disclosure)]
    cases: int
    anchors: int
    score: float
    coverage_ok: bool
    comparable_ok: bool
    score_ok: bool

    @property
    def status(self):
        if not self.coverage_ok:
            return "coverage"
        if not self.comparable_ok:
            return "incomparable"
        if not self.score_ok:
            return "below_line"
        return "valid"

    @property
    def status_label(self):
        return {
            "coverage": "岗目覆盖不足",
            "incomparable": "共同项失效，不可比",
            "below_line": "有证据低于续签线",
            "valid": "继续有效",
        }[self.status]


def _answers_for(rec, person, role):
    return [(a, rec.items[a.item]) for a in rec.answers_by_person.get(person, [])
            if rec.items[a.item].role == role]


def disclosure_affects(answer, item, disclosure, as_of=None):
    """该次揭示是否使这份答卷失去本题证据。

    条件：已实际举行、本人到场、事实相同、揭示早于提交。
    as_of 若给出，则只认台账在该时点前已登记（received <= as_of）的揭示。
    """
    if not disclosure.held:
        return False
    if answer.person not in disclosure.attendees:
        return False
    if (disclosure.case, disclosure.fact) != (item.case, item.fact):
        return False
    if not disclosure.actual < answer.submit:
        return False
    if as_of is not None and disclosure.received > as_of:
        return False
    return True


def exposure_for(rec, answer, item, as_of=None):
    for d in rec.disclosures:
        if disclosure_affects(answer, item, d, as_of):
            return d
    return None


def common_anchor_status(rec, role, as_of=None, planned_by=None):
    """返回 (可用共同题 id -> case, 失效共同题 [(id, disclosure)])。

    as_of 给定时只算该时点前已登记的揭示；planned_by 给定时把
    “排期早于该时刻”的未来讲评也计入，用于命题。
    """
    usable, burned = {}, []
    for item in rec.items.values():
        if item.role != role or not item.common:
            continue
        killer = None
        for d in rec.disclosures:
            if (d.case, d.fact) != (item.case, item.fact):
                continue
            if as_of is not None:
                if not d.held or d.received > as_of:
                    continue
                when = d.actual
            elif planned_by is not None:
                when = d.planned_at()
            else:
                if not d.held:
                    continue
                when = d.actual
            if when is not None and (planned_by is None or when <= planned_by):
                killer = d
                break
        if killer is None:
            usable[item.id] = item.case
        else:
            burned.append((item.id, killer))
    return usable, burned


def _case_score(pairs):
    """同一原案先合并为该案的平均正确率，再跨原案平均。

    同案派生题评估的是同一证据来源，不能当相互独立的同类证据重复计分。
    """
    by_case = defaultdict(list)
    for answer, item in pairs:
        by_case[item.case].append(1.0 if answer.score == SCORE_CORRECT else 0.0)
    if not by_case:
        return 0.0
    return sum(sum(v) / len(v) for v in by_case.values()) / len(by_case)


def assess(rec, person, role, as_of=None):
    kept, removed = [], []
    for answer, item in _answers_for(rec, person, role):
        d = exposure_for(rec, answer, item, as_of)
        (removed if d is not None else kept).append((answer, item, d))
    usable, _ = common_anchor_status(rec, role, as_of=as_of)
    cases = {item.case for _, item, _ in kept}
    anchor_cases = {item.case for _, item, _ in kept if item.id in usable}
    score = _case_score([(a, i) for a, i, _ in kept])
    q = rec.policy[role]
    return RoleEvidence(
        person=person, role=role,
        kept=[(a, i) for a, i, _ in kept],
        removed=removed,
        cases=len(cases),
        anchors=len(anchor_cases),
        score=score,
        coverage_ok=len(cases) >= q.distinct_cases,
        comparable_ok=len(anchor_cases) >= q.anchors,
        score_ok=score >= q.min_score,
    )


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------

def cohort(rec):
    """所有学员 × 其申报岗位的当前证据结论。"""
    out = []
    for person in sorted(rec.applicants, key=lambda s: int(s[1:])):
        app = rec.applicants[person]
        for role in app.roles:
            out.append((app, assess(rec, person, role)))
    return out


def notice_review(rec):
    """逐份通知对比“签发时采用的资料时点”和“当前台账”。

    只有签发后登记、且实际改变了岗位结论的课堂信息才会被列出；
    签发前已登记的信息不算“后来改变”。
    """
    rows = []
    for notice in rec.notices:
        for role in notice.roles:
            then = assess(rec, notice.person, role, as_of=notice.as_of)
            now = assess(rec, notice.person, role)
            later = [d for d in rec.disclosures
                     if d.received > notice.as_of
                     and notice.person in d.attendees
                     and d.held]
            changed = []
            for answer, item, d in now.removed:
                if d.received > notice.as_of:
                    changed.append((item.id, d))
            rows.append(dict(notice=notice, role=role, then=then, now=now,
                             later_disclosures=later,
                             late_removals=changed,
                             changed=then.status != now.status,
                             needs_recheck=(then.status != now.status
                                            and then.status == "valid")))
    return rows


def dispositions(rec):
    """按岗位给出“继续有效 / 暂缓 / 须重审”。

    已发通知且当前仍合格 -> 继续有效；
    已发通知但当前不合格 -> 须重审（原决定所依据的答卷已被后续课堂信息削弱）；
    未发通知且当前合格 -> 可继续有效；
    未发通知且当前不合格 -> 暂缓。
    """
    notices = {}
    for notice in rec.notices:
        for role in notice.roles:
            notices[(notice.person, role)] = notice
    rows = []
    for app, ev in cohort(rec):
        notice = notices.get((app.id, ev.role))
        if notice is not None:
            outcome = "继续有效" if ev.status == "valid" else "须重审"
        else:
            outcome = "继续有效" if ev.status == "valid" else "暂缓"
        rows.append(dict(app=app, ev=ev, notice=notice, outcome=outcome))
    return rows


def person_outcomes(rec):
    """按人汇总：继续有效 / 部分岗位暂缓 / 须重审。"""
    by_person = defaultdict(list)
    for row in dispositions(rec):
        by_person[row["app"].id].append(row)
    out = {}
    for person, rows in by_person.items():
        outcomes = {r["outcome"] for r in rows}
        if "须重审" in outcomes:
            label = "须重审"
        elif outcomes == {"继续有效"}:
            label = "继续有效"
        elif "继续有效" in outcomes:
            label = "部分岗位暂缓"
        else:
            label = "暂缓"
        out[person] = dict(label=label, rows=rows)
    return out


# ---------------------------------------------------------------------------
# 命题可行性
# ---------------------------------------------------------------------------

def _form_ok(rec, combo, policy=None):
    policy = policy or rec.policy
    for role, q in policy.items():
        part = [i for i in combo if i.role == role]
        if len({i.case for i in part}) < q.distinct_cases:
            return False
        if len({i.case for i in part if i.common}) < q.anchors:
            return False
    return True


def _shared_anchors(east_form, west_form, role):
    east = {i.id for i in east_form if i.role == role and i.common}
    west = {i.id for i in west_form if i.role == role and i.common}
    return east & west


def _pair_ok(rec, east_form, west_form):
    for role, q in rec.policy.items():
        if len(_shared_anchors(east_form, west_form, role)) < q.anchors:
            return False
    return True


def _diagnose_pool(rec, items, region):
    """说明某赛区在给定题池下缺什么。"""
    gaps = {}
    for role, q in rec.policy.items():
        part = [i for i in items if i.role == role]
        cases = {i.case for i in part}
        commons = {i.case for i in part if i.common}
        reasons = []
        if len(cases) < q.distinct_cases:
            reasons.append("原案覆盖 %d/%d" % (len(cases), q.distinct_cases))
        if len(commons) < q.anchors:
            reasons.append("可比共同题 %d/%d" % (len(commons), q.anchors))
        if len(part) < q.distinct_cases:
            reasons.append("题量不足")
        gaps[role] = dict(available=len(part), cases=len(cases),
                          common=len(commons), need_cases=q.distinct_cases,
                          need_common=q.anchors, reasons=reasons)
    return gaps


def _burned_for_sit(rec):
    """下一轮考试前会披露的题目：已举行按实际、未举行按排期。"""
    sit = min(f.sit for f in rec.forms)
    out = []
    for item in rec.items.values():
        for d in rec.disclosures:
            if (d.case, d.fact) != (item.case, item.fact):
                continue
            when = d.planned_at()
            if when is not None and when <= sit:
                out.append((item.id, d.id, when))
    return out


def form_plan(rec, allow_inactive=True):
    """为下一轮考核寻找可替代试卷。

    返回每个赛区在“仅用现行有效题”和“允许启用备题”两种题池下的诊断，
    以及最少需要启用哪些备题的可行两区卷对。
    """
    forms = {f.region: f for f in rec.forms}
    cycle = min(f.cycle for f in rec.forms)
    sit = min(f.sit for f in rec.forms)
    size = min(f.size for f in rec.forms)

    burned_future = _burned_for_sit(rec)
    burned_ids = {i for i, _, _ in burned_future}

    def pool(region, active_only):
        return [i for i in rec.items.values()
                if i.form in (region, "common")
                and i.id not in burned_ids
                and (i.active or not active_only)]

    result = dict(sit=sit, cycle=cycle, size=size,
                  burned=burned_future, regions={})
    for region, form in forms.items():
        active_pool = pool(region, True)
        full_pool = pool(region, False)
        active_forms = [c for c in combinations(active_pool, size)
                        if _form_ok(rec, c)]
        full_forms = [c for c in combinations(full_pool, size)
                      if _form_ok(rec, c)] if allow_inactive else []
        result["regions"][region] = dict(
            form=form,
            active_pool=active_pool,
            full_pool=full_pool,
            active_forms=active_forms,
            full_forms=full_forms,
            gaps_only_active=_diagnose_pool(rec, active_pool, region),
            missing_backups=sorted({i.id for i in full_pool
                                    if not i.active} - {i.id for i in active_pool}),
        )

    # 两区联合：先看现行有效题能否成对，再看启用备题的最少方案。
    def pairs(efs, wfs):
        out = []
        for e in efs:
            for w in wfs:
                if _pair_ok(rec, e, w):
                    out.append((e, w))
        return out

    east, west = forms.get("east"), forms.get("west")
    joined = []
    if east and west:
        joined = pairs(result["regions"]["east"]["full_forms"],
                       result["regions"]["west"]["full_forms"])
    result["pairs_full"] = joined
    result["pairs_active"] = pairs(result["regions"].get("east", {}).get("active_forms", []),
                                   result["regions"].get("west", {}).get("active_forms", []))
    if joined:
        joined.sort(key=lambda p: (
            len({i.id for i in p[0] + p[1] if not i.active}),
            len({i.id for i in p[0] + p[1] if i.season == 1}),
            [i.id for i in p[0] + p[1]],
        ))
        minimum = len({i.id for i in joined[0][0] + joined[0][1]
                       if not i.active})
        minimal_sets = Counter()
        for e, w in joined:
            need = tuple(sorted({i.id for i in e + w if not i.active}))
            if len(need) == minimum:
                minimal_sets[need] += 1
        result["min_activation_sets"] = dict(minimal_sets)
        result["min_activation"] = sorted(minimal_sets) and list(
            next(iter(minimal_sets)))
        result["best_pair"] = joined[0]
        result["min_activation_count"] = minimum
    else:
        result["min_activation_sets"] = {}
        result["min_activation"] = None
        result["best_pair"] = None
        result["min_activation_count"] = None
    return result


def missing_case_types(rec, region):
    """在现行有效题池里，按岗位指出缺少的判例类型。

    共同题按定义同时进入两区试卷，所以只统计“未在课堂披露”的有效共同题。
    """
    out = {}
    burned = {i for i, _, _ in _burned_for_sit(rec)}
    for role, q in rec.policy.items():
        cases = {i.case for i in rec.items.values()
                 if i.role == role and i.form in (region, "common")
                 and i.active and i.id not in burned}
        commons = {i.case for i in rec.items.values()
                   if i.role == role and i.common and i.active
                   and i.id not in burned}
        missing = []
        if len(cases) < q.distinct_cases:
            missing.append("不同原案的%s判例（还缺 %d 个）"
                           % (role, q.distinct_cases - len(cases)))
        if len(commons) < q.anchors:
            missing.append("两区共用、且课堂未披露的%s共同判例"
                           "（还缺 %d 个）" % (role, q.anchors - len(commons)))
        out[role] = missing
    return out


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------

def _fmt_pct(value):
    return "%.1f%%" % (value * 100)


def _item_list(items):
    return " ".join(i.id for i in items) if items else "—"


def _disclosure_list(pairs):
    return " ".join("%s(%s)" % (item.id, d.id) for item, d in pairs) if pairs else "—"


def render(rec):
    lines = []
    add = lines.append

    add("# 裁判培训资格证据核对")
    add("")
    add("口径：score=0 为判定正确（1 为失误）；同一原案同岗位只计一次；"
        "只有实际举行、本人到场、事实相同且早于提交的揭示才剔除个人答卷；"
        "共同题依赖的事实一经任何赛区课堂揭示，该题即失去跨卷可比资格。")
    add("")

    # 原案 / 派生题 / 讲评
    add("## 原案、派生试题与讲评出席")
    add("")
    add("| 原案 | 岗位 | 试题 | 卷别 | 依赖事实 | 已举行讲评（出席） | 排期未举行 |")
    add("| --- | --- | --- | --- | --- | --- | --- |")
    for case in sorted({i.case for i in rec.items.values()},
                       key=lambda c: int(c[1:])):
        for role in ("review", "field"):
            items = [i for i in rec.items.values()
                     if i.case == case and i.role == role]
            if not items:
                continue
            for item in sorted(items, key=lambda i: int(i.id[1:])):
                held = [d for d in rec.disclosures
                        if (d.case, d.fact) == (item.case, item.fact) and d.held]
                planned = [d for d in rec.disclosures
                           if (d.case, d.fact) == (item.case, item.fact)
                           and not d.held]
                hd = "；".join("%s %s 出席%d人" %
                               (d.id, d.actual.strftime("%m-%d %H:%M"),
                                len(d.attendees)) for d in held) or "—"
                pd = "；".join("%s 排期%s" %
                               (d.id, d.scheduled.strftime("%m-%d %H:%M"))
                               for d in planned) or "—"
                add("| %s | %s | %s | %s | %s | %s | %s |" %
                    (case, role, item.id, item.form, item.fact, hd, pd))
    add("")

    # 逐岗位结论
    add("## 各岗位资格证据（当前台账）")
    add("")
    add("| 学员 | 赛区 | 班 | 岗位 | 保留答卷 | 剔除 | 原案 | 可比共同题 | 正确率 | 结论 |")
    add("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    by_status = Counter()
    for app, ev in cohort(rec):
        by_status[(ev.role, ev.status)] += 1
        add("| %s | %s | %s | %s | %s | %s | %d/%d | %d/%d | %s | %s |" % (
            app.id, app.region, app.course, ev.role,
            " ".join(a.id.split("-")[1] for a, _ in ev.kept),
            " ".join("%s←%s" % (i.id, d.id) for _, i, d in ev.removed) or "—",
            ev.cases, rec.policy[ev.role].distinct_cases,
            ev.anchors, rec.policy[ev.role].anchors,
            _fmt_pct(ev.score), ev.status_label))
    add("")
    add("汇总：")
    add("")
    add("| 岗位 | 继续有效 | 覆盖不足 | 不可比 | 低于续签线 |")
    add("| --- | --- | --- | --- | --- |")
    for role in ("review", "field"):
        add("| %s | %d | %d | %d | %d |" % (
            role,
            by_status[(role, "valid")],
            by_status[(role, "coverage")],
            by_status[(role, "incomparable")],
            by_status[(role, "below_line")]))
    add("")
    add("说明：三列问题分别呈现。覆盖不足 = 保留答卷的原案数低于门槛；"
        "不可比 = 每一岗位能在两区之间共用的共同题少于锚题要求；"
        "低于续签线 = 保留答卷的正确率低于该岗位分数线。"
        "三项都过才算继续有效；不可比不能靠删掉疑似答案后沿用原分数线解决。")
    add("")

    # 按人给出处理口径
    outcomes = person_outcomes(rec)
    add("## 按岗位与通知日期给出的资格处理")
    add("")
    add("| 学员 | 赛区 | 已发通知 | 岗位结论 | 处理 |")
    add("| --- | --- | --- | --- | --- |")
    for person in sorted(outcomes, key=lambda s: int(s[1:])):
        item = outcomes[person]
        notice = next((r["notice"] for r in item["rows"] if r["notice"]), None)
        issued = notice.issued.strftime("%m-%d %H:%M") if notice else "—"
        detail = "；".join("%s：%s" % (r["ev"].role, r["ev"].status_label)
                          for r in item["rows"])
        add("| %s | %s | %s | %s | %s |" % (
            person, item["rows"][0]["app"].region, issued, detail,
            item["label"]))
    add("")
    add("处理口径：已发通知且当前仍合格 = 继续有效；已发通知但当前不再合格 = 须重审；"
        "未发通知且当前合格 = 继续有效，不合格 = 暂缓。"
        "同一人不同岗位各自判断，因此会出现“部分岗位暂缓”。")
    add("")

    # 通知复盘
    add("## 已发资格通知复盘")
    add("")
    add("| 通知 | 学员 | 岗位 | 签发时点 | 当时结论 | 当前结论 | 后来改变判断的课堂信息 |")
    add("| --- | --- | --- | --- | --- | --- | --- |")
    for row in notice_review(rec):
        n = row["notice"]
        if row["changed"] and row["late_removals"]:
            changed = "；".join("%s 因 %s（%s 实际举行，%s 登记）" %
                               (item_id, d.id,
                                d.actual.strftime("%m-%d %H:%M"),
                                d.received.strftime("%m-%d %H:%M"))
                               for item_id, d in row["late_removals"])
        elif row["changed"]:
            changed = "后续课堂信息使可比共同题失效"
        else:
            changed = "—"
        add("| %s | %s | %s | %s | %s | %s | %s |" % (
            n.id, n.person, row["role"], n.as_of.strftime("%m-%d %H:%M"),
            row["then"].status_label, row["now"].status_label, changed))
    add("")
    add("正常执裁仍以主管确认的资格通知为准；上述复盘只用于决定是否重审。")
    add("")

    # 未来批次
    plan = form_plan(rec)
    add("## 下一轮（cycle %d，%s）命题可行性" % (
        plan["cycle"], plan["sit"].strftime("%Y-%m-%d %H:%M")))
    add("")
    add("已排期或已举行的讲评若早于考试时刻，相应题目不得再用：")
    add("")
    add("| 试题 | 讲评 | 时点 | 状态 |")
    add("| --- | --- | --- | --- |")
    for item_id, did, when in plan["burned"]:
        held = any(d.id == did and d.held for d in rec.disclosures)
        add("| %s | %s | %s | %s |" % (
            item_id, did, when.strftime("%m-%d %H:%M"),
            "已举行" if held else "已排期未举行"))
    add("")
    for region in ("east", "west"):
        r = plan["regions"][region]
        add("### %s（%s，题量 %d）" % (region, r["form"].id, plan["size"]))
        add("")
        add("仅用现行有效题：可用题 %d（%s）。" % (
            len(r["active_pool"]), _item_list(r["active_pool"])))
        add("")
        add("| 岗位 | 可用题 | 可覆盖原案 | 可比共同题 | 缺口 |")
        add("| --- | --- | --- | --- | --- |")
        for role in ("review", "field"):
            g = r["gaps_only_active"][role]
            add("| %s | %d | %d/%d | %d/%d | %s |" % (
                role, g["available"], g["cases"], g["need_cases"],
                g["common"], g["need_common"],
                "；".join(g["reasons"]) or "无"))
        add("")
        if r["active_forms"]:
            add("现行有效题可组成 %d 套合规单区卷。" % len(r["active_forms"]))
        else:
            add("现行有效题无法组成合规单区卷；启用备题后可行卷 %d 套，"
                "涉及备题 %s。" % (len(r["full_forms"]),
                                  " ".join(r["missing_backups"]) or "无"))
        add("")
    add("### 缺哪类判例")
    add("")
    add("| 赛区 | 岗位 | 现行有效题池中缺少的判例类型 |")
    add("| --- | --- | --- |")
    for region in ("east", "west"):
        for role, missing in missing_case_types(rec, region).items():
            add("| %s | %s | %s |" % (
                region, role, "；".join(missing) or "无"))
    add("")
    add("### 两区共用锚题")
    add("")
    if plan["pairs_active"]:
        add("仅用现行有效题即可组成 %d 组可互比的两区卷对。" % len(plan["pairs_active"]))
    else:
        add("仅用现行有效题无可互比卷对。")
        if plan["pairs_full"]:
            add("允许启用备题后可行卷对 %d 组；最少启用 %d 道备题：%s。" % (
                len(plan["pairs_full"]), plan["min_activation_count"],
                " ".join(plan["min_activation"])))
            east_form, west_form = plan["best_pair"]
            add("")
            add("示例方案（两区共享共同原案，保证跨卷可比）：")
            add("")
            add("| 赛区 | 试卷 | 共用共同题 |")
            add("| --- | --- | --- |")
            for region, combo in (("east", east_form), ("west", west_form)):
                shared = sorted({i.id for i in combo if i.common})
                shared_other = sorted({i.id for i in combo if i.common} &
                                      {i.id for i in (west_form if region == "east"
                                                       else east_form) if i.common})
                add("| %s | %s | %s |" % (region, _item_list(combo),
                                          " ".join(shared_other) or "—"))
            add("")
            add("启用备题：%s。" % " ".join(plan["min_activation"]))
            if len(plan["min_activation_sets"]) > 1:
                add("")
                add("其他同样只需启用 1 道备题的可行方案：%s。" % "、".join(
                    " ".join(k) for k in plan["min_activation_sets"]
                    if list(k) != plan["min_activation"]))
            add("")
            add("最近上线的备题（I18/I19/I22 一类）不能直接顶替："
                "它们并未补上两区共用的锚题，单靠它们无法同时满足"
                "原案覆盖与跨卷可比。")
        else:
            add("即使启用全部备题也无法组成可互比的两区卷。")
    add("")
    add("结论：现行有效题不足以同时满足原案覆盖与跨卷锚题；"
        "不能直接取最近上线的备题，因为 D3 排期仍会披露同一原案。"
        "需要按上表启用未被披露的原案派生题，并保留两区共用的共同题。")
    return "\n".join(lines)


def main(argv: Sequence | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", default=str(DEFAULT_RECORDS))
    parser.add_argument("--markdown", action="store_true",
                        help="输出 Markdown 报告（默认输出同一内容）")
    args = parser.parse_args(argv)
    rec = Records(Path(args.records))
    problems = rec.verify()
    if problems:
        raise SystemExit("记录校验失败：\n" + "\n".join(problems))
    print(render(rec))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
