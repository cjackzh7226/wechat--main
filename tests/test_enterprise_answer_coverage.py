"""Regression cases for retrieved knowledge disappearing before answer validation."""
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory import StyleMemory
from reply_engine import ENTERPRISE_NO_ANSWER_REPLY, ReplyEngine
from settings import AppSettings


class EnterpriseAnswerCoverageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.memory = StyleMemory(Path(self.tmp.name) / 'memory.json')
        self.engine = ReplyEngine(AppSettings(api_key='test-key', reply_source_mode='model_memory'), self.memory)

    def response(self, data):
        response = Mock()
        response.json.return_value = {'choices': [{'message': {'content': json.dumps(data)}}]}
        return response

    def test_complete_answer_and_conditions_reach_judge(self):
        answer = '填报错误时应提交情况说明。' * 20 + '确实超龄时须按章程完成换届并提交负责人变动备案材料。'
        self.memory.examples = [{
            'source': 'enterprise_annual_report_kb',
            'question': '负责人年龄超70周岁怎么办？',
            'reply': answer, 'organization_type': '社会团体', 'module': '整改',
        }]

        def judge(_url, **kwargs):
            payload = json.loads(kwargs['json']['messages'][1]['content'])
            self.assertEqual(payload['candidates'][0]['answer'], answer)
            self.assertEqual(payload['candidates'][0]['organization_type'], '社会团体')
            self.assertEqual(payload['customer_question'], '负责人年龄超70周岁怎么整改')
            return self.response({'chosen': 1, 'consistent': True})

        with patch('reply_engine.requests.post', side_effect=judge) as post:
            replies = self.engine.generate('[对方] 负责人年龄超70周岁怎么整改')

        post.assert_called_once()
        self.assertEqual(replies, [answer])

    def test_screenshot_age_question_can_select_fifth_retrieved_answer(self):
        # Read the checked-in public seed only; never save to it or load user data.
        seed = Path(__file__).resolve().parents[1] / 'release_seed/user_data/memory.json'
        self.engine.memory = StyleMemory(seed).load()
        ranked = self.engine.memory.enterprise_candidates('负责人超龄怎么整改', limit=6)
        correct = next(c for c in ranked if '负责人年龄超70周岁' in c['question'])
        self.assertGreaterEqual(ranked.index(correct), 3)

        def judge(_url, **kwargs):
            payload = json.loads(kwargs['json']['messages'][1]['content'])
            candidate = next(c for c in payload['candidates'] if c['question'] == correct['question'])
            return self.response({'chosen': candidate['id'], 'consistent': True})

        with patch('reply_engine.requests.post', side_effect=judge) as post:
            replies = self.engine.generate('[对方] 负责人超龄怎么整改')

        post.assert_called_once()
        self.assertEqual(replies, [correct['reply']])
        self.assertFalse(self.engine.last_error)

    def test_clear_local_lead_still_supplies_alternative_answers(self):
        seed = Path(__file__).resolve().parents[1] / 'release_seed/user_data/memory.json'
        self.engine.memory = StyleMemory(seed).load()
        query = '怎么查以前的年度报告'
        ranked = self.engine.memory.enterprise_candidates(query, limit=6)
        self.assertEqual(ranked[0]['enterprise_local_confident'], '1')

        with patch('reply_engine.requests.post', return_value=self.response({'chosen': 1, 'consistent': True})) as post:
            replies = self.engine.generate(query)

        payload = json.loads(post.call_args.kwargs['json']['messages'][1]['content'])
        self.assertGreater(len(payload['candidates']), 1)
        self.assertEqual(replies, [ranked[0]['reply']])

    def test_user_ratio_statement_returns_full_original_rectification_answer(self):
        seed = Path(__file__).resolve().parents[1] / 'release_seed/user_data/memory.json'
        self.engine.memory = StyleMemory(seed).load()
        # User-supplied short statement, plus OCR spacing; no invented question suffix.
        for query in ['负责人超过理事数的1/3', '负责人超过理事数的 1/3']:
            with self.subTest(query=query):
                selected = []

                def judge(_url, **kwargs):
                    payload = json.loads(kwargs['json']['messages'][1]['content'])
                    self.assertEqual(payload['customer_question'], query)
                    candidate = next(c for c in payload['candidates'] if
                                     '负责人超过理事数的1/3' in c['question'].replace(' ', ''))
                    self.assertNotIn('常务理事数的1/2', candidate['question'])
                    self.assertIn('情形二', candidate['answer'])
                    selected.append(candidate['answer'])
                    return self.response({'chosen': candidate['id'], 'consistent': True})

                with patch('reply_engine.requests.post', side_effect=judge) as post:
                    replies = self.engine.generate('[对方] ' + query)
                post.assert_called_once()
                self.assertEqual(replies, selected)
                self.assertFalse(self.engine.last_error)

    def test_no_api_or_invalid_response_is_not_reported_as_missing_knowledge(self):
        self.memory.examples = [{
            'source': 'enterprise_annual_report_kb',
            'question': '往年年度工作报告如何查看？',
            'reply': '历史报告请到公示页面查询。',
        }]
        for output in ['not json', '{"chosen": 7, "consistent": true}', '{"chosen": true, "consistent": true}']:
            with self.subTest(output=output):
                response = Mock()
                response.json.return_value = {'choices': [{'message': {'content': output}}]}
                with patch('reply_engine.requests.post', return_value=response):
                    self.assertEqual(self.engine.generate('想查一下之前的年报'), [])
                self.assertTrue(self.engine.last_error)
        self.engine.settings.api_key = ''
        with patch('reply_engine.requests.post') as post:
            self.assertEqual(self.engine.generate('想查一下之前的年报'), [])
        post.assert_not_called()
        self.assertIn('未配置', self.engine.last_error)

    def test_judge_failure_clears_candidates_and_never_sends_group_reply(self):
        import main
        self.memory.examples = [{
            'source': 'enterprise_annual_report_kb',
            'question': '往年年度工作报告如何查看？',
            'reply': '历史报告请到公示页面查询。',
        }]
        app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
        app.engine = self.engine
        app.engine_call_lock = threading.Lock()
        app.group_generation_lock = threading.Lock()
        app.group_generation_inflight = True
        app.pending_group_generation = None
        app.queue = Queue()
        app._group_send_preflight_allows = Mock(return_value=(True, ''))
        app.group_handler = Mock()
        app._send_group_reply = Mock()
        question = '想查一下之前的年报'
        job = (SimpleNamespace(), question, question, question, '测试成员', '测试群')
        with patch('reply_engine.requests.post', side_effect=RuntimeError('timeout')):
            app._group_generation_worker(job)

        app._group_send_preflight_allows.assert_not_called()
        app._send_group_reply.assert_not_called()
        self.assertFalse(app.group_generation_inflight)
        status = app.queue.get_nowait()[1]
        self.assertIn('timeout', status)
        self.assertNotIn('未找到', status)

    def test_judge_rejection_keeps_no_answer_and_cannot_generate_its_own_reply(self):
        self.memory.examples = [{
            'source': 'enterprise_annual_report_kb',
            'question': '往年年度工作报告如何查看？',
            'reply': '历史报告请到公示页面查询。',
        }]
        with patch('reply_engine.requests.post', return_value=self.response({
            'chosen': 1, 'consistent': False, 'answer': '模型额外编写的答案不可发送',
        })):
            replies = self.engine.generate('想查一下之前的年报')
        self.assertEqual(replies, [ENTERPRISE_NO_ANSWER_REPLY])
        self.assertFalse(self.engine.last_error)

    def test_conflicting_and_negligible_candidates_cannot_be_selected(self):
        ranked = [
            {'question': '历史报告查询', 'reply': '查询方法', 'enterprise_local_score': 60,
             'enterprise_local_confident': '1', 'enterprise_conflict_count': '0'},
            {'question': '今年报告修改', 'reply': '修改方法', 'enterprise_local_score': 45,
             'enterprise_conflict_count': '2'},
            {'question': '无关条目', 'reply': '无关答案', 'enterprise_local_score': 2,
             'enterprise_conflict_count': '0'},
        ]
        with patch.object(self.memory, 'enterprise_candidates', return_value=ranked):
            with patch('reply_engine.requests.post', return_value=self.response({'chosen': 2, 'consistent': True})) as post:
                replies = self.engine.generate('怎么查以前的报告')
        payload = json.loads(post.call_args.kwargs['json']['messages'][1]['content'])
        self.assertEqual([c['answer'] for c in payload['candidates']], ['查询方法'])
        self.assertEqual(replies, [])
        self.assertIn('超出候选范围', self.engine.last_error)


if __name__ == '__main__':
    unittest.main()
