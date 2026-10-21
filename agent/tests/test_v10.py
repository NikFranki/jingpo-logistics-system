import unittest

from tools import _collect_pages, _result
from state import TurnLimitError
from eval_scoring import score_answer, failed, timestamp_matches, fact_matches, scoped_status_matches


class PaginationTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_page_is_not_the_whole_collection(self):
        for requested, count in [(1, 2), (2, 2), (3, 1), (99, 0)]:
            async def fetch(page):
                return {'data': {'items': [{'id': str(i)} for i in range(count)],
                                 'total': 5}, 'request_id': None}
            result = _result()
            _, meta = await _collect_pages(fetch, result, requested, 2, False)
            self.assertTrue(meta['complete'])
            self.assertFalse(meta['all_records_complete'])
            self.assertFalse(result['meta']['collections']['items']['complete'])

    async def test_first_page_can_cover_all_including_empty_collection(self):
        for count in (0, 2):
            async def fetch(page):
                return {'data': {'items': [{'id': str(i)} for i in range(count)],
                                 'total': count}, 'request_id': None}
            _, meta = await _collect_pages(fetch, _result(), 1, 10, False)
            self.assertTrue(meta['all_records_complete'])

    async def test_full_traversal_and_budget_exhaustion(self):
        for exhausted in (False, True):
            async def fetch(page):
                if exhausted and page == 2:
                    raise TurnLimitError('HTTP_CALL_LIMIT', 'budget')
                return {'data': {'items': [{'id': str(page)}], 'total': 3}, 'request_id': None}
            _, meta = await _collect_pages(fetch, _result(), 1, 1, True)
            self.assertEqual(meta['all_records_complete'], not exhausted)

    async def test_empty_gap_and_duplicates_cannot_claim_complete(self):
        for repeated in (False, True):
            async def fetch(page):
                items = [{'id': '1'}] if page == 1 or repeated else []
                return {'data': {'items': items, 'total': 3}, 'request_id': None}
            _, meta = await _collect_pages(fetch, _result(), 1, 1, True)
            self.assertFalse(meta['all_records_complete'])

    async def test_budget_before_first_response_is_not_empty_complete(self):
        async def fetch(page):
            raise TurnLimitError('HTTP_CALL_LIMIT', 'budget')
        _, meta = await _collect_pages(fetch, _result(), 1, 10, False)
        self.assertFalse(meta['all_records_complete'])


class ScoringTests(unittest.TestCase):
    def test_equivalent_times(self):
        for answer in ['2026-10-17T08:00:00Z', '2026-10-17 08:00:00（UTC）',
                       '2026-10-17 16:00+08:00']:
            self.assertTrue(timestamp_matches(answer, '2026-10-17T08:00:00Z'))

    def test_existing_iso_time_requirements_use_instant_comparison(self):
        case = {'must': ['OVERDUE', '2026-10-18T08:00:00Z']}
        self.assertFalse(failed(score_answer(case, 'OVERDUE，2026-10-18 08:00:00Z')))
        self.assertTrue(failed(score_answer(case, 'OVERDUE，2026-10-18 08:01:00Z')))

    def test_wrong_or_ambiguous_times(self):
        for answer in ['2026-10-17 08:00', '2026-10-17T08:01:00Z',
                       '2026-10-17 08:00+08:00', '2026-10-18T08:00:00Z']:
            self.assertFalse(timestamp_matches(answer, '2026-10-17T08:00:00Z'))

    def test_not_found_equivalents(self):
        case = {'semantic_check': 'not_found_not_failure'}
        for answer in ['本人可见范围内未找到该运单记录，并非查询服务失败。',
                       '查无此记录，查询服务正常。', '查询已正常完成，但未查到匹配记录。', '没有查到运单，未出现查询服务失败。', '未查到运单记录，无报错。',
                       '未找到运单记录，并非查询服务失败。不能据此断定该运单在系统中一定不存在。']:
            self.assertFalse(failed(score_answer(case, answer)))

    def test_negative_and_contradictory_answers(self):
        case = {'semantic_check': 'not_found_not_failure'}
        for answer in ['查询服务失败，无法确认。', '查询已不正常，未查到匹配记录。', '未找到运单记录。',
                       '查无此记录，无报错。但服务已经故障。',
                       '查无此记录，并非服务失败，系统中一定不存在。']:
            self.assertTrue(failed(score_answer(case, answer)))

    def test_cancel_time_alone_is_not_sufficient(self):
        case = {'must': ['CANCELLED', '演示取消'], 'expected_timestamps': ['2026-10-17T08:00:00Z']}
        self.assertTrue(failed(score_answer(case, '2026-10-17T08:00:00Z')))
        self.assertFalse(failed(score_answer(case, 'CANCELLED，演示取消，2026-10-17T08:00:00Z')))


class PresenceTests(unittest.TestCase):
    def test_mixed_statuses_are_not_exposed_in_presence_projection(self):
        from tools import _shipment_presence_result
        items = [
            {'id': '1', 'order_no': 'ORD-1', 'shipment': {'id': '11', 'shipment_no': 'SHP-11', 'stage': 'CANCELLED'}},
            {'id': '2', 'order_no': 'ORD-2', 'shipment': None},
            {'id': '3', 'order_no': 'ORD-3', 'shipment': {'id': '13', 'shipment_no': 'SHP-13', 'stage': 'AT_STATION'}},
        ]
        result = _shipment_presence_result(items, {'1', '2', '3'}, {'total': 3, 'all_records_complete': True})
        self.assertEqual(result['summary']['with_shipment'], 2)
        self.assertEqual(result['summary']['without_shipment'], 1)
        self.assertEqual([x['id'] for x in result['without_shipment']], ['2'])
        self.assertTrue(result['summary']['all_records_complete'])
        self.assertNotIn('stage', str(result))

    def test_unverified_null_is_unknown_and_unread_is_not_absent(self):
        from tools import _shipment_presence_result
        result = _shipment_presence_result([{'id':'2','order_no':'ORD-2','shipment':None}], set(),
                                          {'total': 5, 'all_records_complete': False})
        self.assertEqual(result['summary']['without_shipment'], 0)
        self.assertEqual(result['summary']['unknown'], 1)
        self.assertEqual(result['summary']['unread'], 4)
        self.assertFalse(result['summary']['all_records_complete'])


class FixtureContractTests(unittest.TestCase):
    def test_fixture_is_consistent(self):
        from eval_v10 import fixture_data, validate_fixture
        validate_fixture(*fixture_data())

    def test_conflicting_endpoint_state_is_rejected(self):
        from eval_v10 import fixture_data, validate_fixture
        fixture = fixture_data()
        fixture[0][3]['shipment']['stage'] = 'PENDING_PICKUP'
        with self.assertRaisesRegex(AssertionError, 'order/shipment mismatch'):
            validate_fixture(*fixture)

    def test_wrong_destination_and_history_are_rejected(self):
        from eval_v10 import fixture_data, validate_fixture
        for field in ('destination', 'history'):
            fixture = fixture_data()
            if field == 'destination':
                fixture[1][0]['destination_station_id'] = '2'
            else:
                fixture[4][-1]['legs'].pop()
            with self.assertRaises(AssertionError):
                validate_fixture(*fixture)

    def test_core_answer_and_evidence_are_separate(self):
        from eval_scoring import assessment
        result = assessment({'missing_terms':['cancelled'], 'missing_references':[]})
        self.assertTrue(result['evidence_ok'])
        self.assertFalse(result['core_answer_ok'])
        self.assertEqual(result['additional_claims_review'], 'pending')


class PresenceIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_checks_associations_and_returns_program_counts(self):
        from eval_v10 import serve_fixture, run_case, CASES
        from test_harness import FakeModel, call
        from langchain_core.messages import AIMessage
        server, base = serve_fixture()
        try:
            case = next(x for x in CASES['cases'] if x['id'] == 'HOLD-03')
            result = await run_case(FakeModel([
                call('search_orders', {'all_pages': True, 'include_shipment': True}),
                AIMessage(content='ORD-0003 尚无运单'),
            ]), base, case)
            self.assertEqual(result['missing_tool_facts'], [])
            self.assertEqual(result['missing_references'], [])
            self.assertEqual(result['http_calls'], 14)
            self.assertTrue(result['tool_results'][0]['data_summary']['all_records_complete'])
        finally:
            server.shutdown()
            server.server_close()


class StatusAliasTests(unittest.TestCase):
    def test_cancelled_chinese_translation_passes_without_english_code(self):
        self.assertFalse(failed(score_answer({'must': ['CANCELLED']}, '订单已取消。')))

    def test_wrong_status_does_not_match_cancelled(self):
        self.assertTrue(failed(score_answer({'must': ['CANCELLED']}, '订单仍在运输中。')))

    def test_aliases_are_exact_and_bounded(self):
        self.assertTrue(fact_matches('当前阶段为在途。', 'IN_TRANSIT'))
        self.assertFalse(fact_matches('运输状态未知。', 'IN_TRANSIT'))
        self.assertFalse(scoped_status_matches('ORD-0002 已发货。关联运单 SHP-0102 已取消。', 'ORD-0002', 'CANCELLED'))
        self.assertFalse(scoped_status_matches('ORD-0002 已取消。关联运单 SHP-0102 运输中。', 'SHP-0102', 'CANCELLED'))
        self.assertTrue(scoped_status_matches('ORD-0002 的订单状态为 CANCELLED。', 'ORD-0002', 'CANCELLED'))
