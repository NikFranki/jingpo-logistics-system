"""Deterministic answer checks; machine pass does not replace factual review."""
from datetime import datetime, timezone
import re

SCORING_VERSION = 'v10-5'


def timestamp_matches(answer, expected):
    # Require an explicit timezone. Naive times must not silently become UTC.
    pattern = r'(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2})?)\s*[（(]?\s*(Z|UTC(?:[+-]\d{2}:?\d{2})?|[+-]\d{2}:?\d{2})'
    found = []
    for day, clock, zone in re.findall(pattern, answer, re.I):
        zone = zone.upper().replace('UTC', '')
        zone = '+00:00' if zone in ('', 'Z') else zone
        try:
            found.append(datetime.fromisoformat(f'{day}T{clock}{zone}').astimezone(timezone.utc))
        except ValueError:
            pass
    target = datetime.fromisoformat(expected.replace('Z', '+00:00'))
    return target in found


STATUS_TERMS = {
    'CANCELLED': ('已取消', '已经取消', '取消状态'),
    'IN_TRANSIT': ('运输中', '在途', '正在运输'),
    'PENDING_PICKUP': ('待揽收', '待提货', '等待揽收', '待取件'),
    'AT_STATION': ('在站点', '在站', '已到站'),
    'PLANNED': ('计划关联', '计划中', '未来计划'),
    'ACTIVE': ('当前执行', '当前关联', '执行中'),
    'RELEASED': ('已解除', '已解除关联', '关联已失效'),
    'OVERDUE': ('超时', '已超时'),
    'WAITING_PREDECESSOR': ('等待前段', '前段未就绪', '等待前段就绪'),
    'NOT_APPLICABLE': ('不适用', '当前不适用'),
    'NOT_CONFIRMED': ('未确认', '无法确认'),
    'CONFIRMED': ('已确认',),
    'NEEDS_RECONFIRMATION': ('需重新确认', '需要重新确认'),
    'NONE': ('无延误提示', '当前无延误提示'),
}


def fact_matches(answer, expected):
    if expected.lower() in answer.lower():
        return True
    variants = STATUS_TERMS.get(expected)
    return bool(variants and any(value.lower() in answer.lower() for value in variants))



def scoped_status_matches(answer, subject, status):
    subject = re.escape(subject)
    alternatives = '|'.join(re.escape(term) for term in (status, *STATUS_TERMS.get(status, ())))
    for clause in re.split(r'[。！？；;\n]', answer):
        if re.search(subject + r'[^。！？；;\n]{0,48}(?:' + alternatives + r')', clause, re.I):
            return True
        if re.search(r'(?:' + alternatives + r')[^。！？；;\n]{0,48}' + subject, clause, re.I):
            return True
    return False

def score_answer(case, answer):
    time_terms = [x for x in case.get('must', []) if re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', x)]
    missing = [x for x in case.get('must', []) if x not in time_terms and not fact_matches(answer, x)]
    any_missing = [xs for xs in case.get('must_any', []) if not any(x.lower() in answer.lower() for x in xs)]
    patterns = [x for x in case.get('answer_fact_patterns', []) if not re.search(x, answer, re.I | re.S)]
    forbidden = [x for x in case.get('forbid', []) if x.lower() in answer.lower()]
    missing_status = [fact for fact in case.get('must_status', [])
                      if not scoped_status_matches(answer, fact['subject'], fact['status'])]
    semantic = []
    for expected in [*time_terms, *case.get('expected_timestamps', [])]:
        if not timestamp_matches(answer, expected):
            semantic.append('timestamp:' + expected)
    if case.get('semantic_check') == 'not_found_not_failure':
        clean = answer.replace('**', '')
        absent = re.search(r'查无此记录|没有查到.{0,15}(?:运单|记录)|未找到.{0,15}(?:运单|记录)|没有找到.{0,15}(?:运单|记录)|未查到.{0,15}(?:运单|记录)', clean)
        healthy = re.search(r'(?:并非|不是|不属于|未出现|没有出现|非)(?:查询)?(?:服务失败|服务异常|服务故障|超时)|查询(?:服务)?(?:已|已经)?(?:正常|成功)|无报错', clean)
        # Contradictory assertions must fail even when a correct phrase appears too.
        assertions = '。'.join(part for part in re.split(r'[。；;\n]', clean)
                                if not re.search(r'不能.{0,12}(?:断定|确认|判断)|无法.{0,8}(?:断定|确认|判断)', part))
        contradiction = re.search(r'(?<!不)(?<!并)(?<!而)是(?:查询)?服务失败|(?:服务|查询)(?:已经|发生|出现)(?:故障|失败|超时)|(?:系统中|全库)(?:一定|肯定|确定)不存在', assertions)
        if not absent or not healthy or contradiction:
            semantic.append('not_found_not_failure')
    return dict(missing_terms=missing, missing_any_term_groups=any_missing,
                missing_answer_fact_patterns=patterns, missing_status_facts=missing_status, forbidden_terms_found=forbidden,
                missing_semantic_facts=semantic)


def failed(item):
    return any(item.get(k) for k in ('error_type', 'stop_reason', 'missing_references',
        'missing_terms', 'missing_any_term_groups', 'missing_answer_fact_patterns', 'missing_status_facts',
        'missing_tool_facts', 'forbidden_terms_found', 'missing_semantic_facts'))


def assessment(item):
    """Keep evidence coverage separate from answer quality and review."""
    runtime_ok = not (item.get("error_type") or item.get("stop_reason"))
    return {
        "runtime_ok": runtime_ok,
        "core_answer_ok": runtime_ok and not any(item.get(key) for key in (
            "missing_terms", "missing_any_term_groups", "missing_answer_fact_patterns", "missing_status_facts", "missing_semantic_facts")),
        "evidence_ok": runtime_ok and not (item.get("missing_references") or item.get("missing_tool_facts")),
        "forbidden_claims_ok": not bool(item.get("forbidden_terms_found")),
        "additional_claims_review": item.get("additional_claims_review", "pending"),
    }
