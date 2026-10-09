"""Deterministic API/stage/memory metrics tests; no providers or robot required."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'modules'))
from experiment_log import start_run, log_dir, logged_tool
from model_metrics import ModelRequest, record_model_request, normalize_usage
from metrics_report import summarize
from stage_metrics import start_stage, finish_stage


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {'CARS_LOG_DIR': self.temp.name})
        env.start()
        self.addCleanup(env.stop)
        self.run = start_run('metrics', {'scenario': 'same_target', 'outcome': 'success'})

    def rows(self, run=None):
        return [json.loads(line) for line in (log_dir() / f'run_{run or self.run}.jsonl').read_text().splitlines()]

    def test_api_timing_usage_and_run_pinning(self):
        with patch('model_metrics.time.perf_counter', side_effect=[10.0, 10.25, 12.0]):
            with ModelRequest(provider='openai', model='configured-model', stage=0, purpose='planning') as request:
                request.first_token()
                request.set_usage({'input_tokens': 100, 'output_tokens': 20, 'cached_input_tokens': 40,
                                   'reasoning_output_tokens': 5},
                                  context_tokens={'stm_tokens': 8, 'ltm_tokens': 30,
                                                  'method': 'tokenizer', 'tokenizer': 'fixture-v1'})
                start_run('next_run')
        record = self.rows()[-1]
        self.assertEqual(record['latency_seconds'], 2.0)
        self.assertEqual(record['ttft_seconds'], .25)
        self.assertEqual(record['usage']['total_tokens'], 120)
        self.assertEqual(record['run_id'], self.run)
        self.assertEqual(len(self.rows('next_run')), 1)

    def test_error_and_cancellation_are_logged_and_propagated(self):
        with self.assertRaises(TimeoutError):
            with ModelRequest(provider='google', model='configured-gemini', stage=3):
                raise TimeoutError('sensitive data must not be logged')
        self.assertEqual(self.rows()[-1]['status'], 'error')
        self.assertNotIn('sensitive', json.dumps(self.rows()))
        with self.assertRaises(asyncio.CancelledError):
            with ModelRequest(provider='google', model='configured-gemini'):
                raise asyncio.CancelledError()
        self.assertEqual(self.rows()[-1]['status'], 'cancelled')

    def test_unknown_usage_and_failures_do_not_bias_completed_latency(self):
        for latency, status, usage in ((2, 'completed', {'input_tokens': 10, 'output_tokens': 5}),
                                       (4, 'completed', None), (100, 'error', None)):
            record_model_request(run_id=self.run, provider='google', model='gemini-configured',
                                 stage=3, purpose='planning', latency_seconds=latency, status=status, usage=usage)
        group = summarize(self.rows())['model_groups'][0]
        self.assertEqual(group['completed_latency_seconds']['mean'], 3)
        self.assertEqual(group['tokens_all_attempts']['input_tokens'],
                         {'known_sum': 10, 'reported_requests': 1, 'missing_requests': 2})
        self.assertIsNone(group['tokens_all_attempts']['cached_input_tokens']['known_sum'])

    def test_reject_invalid_metrics(self):
        for usage in ({'input_tokens': -1}, {'input_tokens': True},
                      {'input_tokens': 3, 'output_tokens': 2, 'total_tokens': 20},
                      {'input_tokens': 5, 'cached_input_tokens': 6}, {'invented': 10}):
            with self.assertRaises(ValueError):
                normalize_usage(usage)
        for latency in (float('nan'), float('inf'), -2, True):
            with self.assertRaises(ValueError):
                record_model_request(run_id=self.run, provider='test', model='test', stage=0,
                                     purpose='planning', latency_seconds=latency)
        with self.assertRaises(ValueError):
            ModelRequest(provider='test', model='test', stage=True)

    def test_memory_payload_sizes_are_not_token_estimates(self):
        @logged_tool('CognitiveMemory')
        def read_short_term_memory():
            return '\u00e7'
        @logged_tool('CognitiveMemory')
        def update_short_term_memory(intention):
            return 'updated'
        read_short_term_memory()
        read_short_term_memory()
        update_short_term_memory('go')
        records = self.rows()
        metric = records[2]['memory_metrics']
        self.assertEqual(metric['payload'], {'characters': 1, 'utf8_bytes': 2})
        self.assertIsNone(metric['tokens'])
        totals = summarize(records)['memory_payloads']
        self.assertEqual(totals[0]['calls'], 2)
        self.assertEqual(totals[0]['output_characters'], 2)
        self.assertEqual(totals[0]['output_utf8_bytes'], 4)
        self.assertEqual(totals[1]['operation'], 'write')

    def test_stage_timer_rotation_duplicate_and_unfinished(self):
        stage_id = start_stage(0, 'openai', 'configured-model')
        start_run('rotated')
        record = finish_stage(stage_id, 'completed')
        self.assertEqual(record['run_id'], self.run)
        self.assertGreaterEqual(record['observed_stage_seconds'], 0)
        self.assertNotIn('latency_seconds', record)
        with self.assertRaises(ValueError):
            finish_stage(stage_id, 'completed')
        start_stage(3, 'google', 'configured-gemini')
        report = summarize(self.rows() + self.rows('rotated'))
        self.assertEqual(len(report['unfinished_stages']), 1)
        self.assertEqual(report['observed_stage_groups'][0]['stage'], 0)

    def test_report_rejects_duplicate_requests_and_events(self):
        record_model_request(run_id=self.run, provider='test', model='test', stage=0,
                             purpose='planning', latency_seconds=1, request_id='same')
        with self.assertRaises(ValueError):
            summarize(self.rows() * 2)
        with self.assertRaises(ValueError):
            record_model_request(run_id=self.run, provider='test', model='test', stage=0,
                                 purpose='planning', latency_seconds=1, request_id='same')

    def test_cli_import_and_report_from_another_directory(self):
        import subprocess
        modules = Path(__file__).resolve().parents[1] / 'modules'
        record = Path(self.temp.name) / 'host_record.json'
        record.write_text(json.dumps(dict(run_id=self.run, provider='google', model='configured-gemini',
                                         stage=3, purpose='planning', latency_seconds=2.0,
                                         usage={'input_tokens': 20, 'output_tokens': 10})))
        imported = subprocess.run([sys.executable, str(modules / 'model_metrics.py'), str(record)],
                                  cwd=self.temp.name, capture_output=True, text=True)
        self.assertEqual(imported.returncode, 0, imported.stderr)
        report = subprocess.run([sys.executable, str(modules / 'metrics_report.py'),
                                 str(log_dir() / f'run_{self.run}.jsonl')],
                                cwd=self.temp.name, capture_output=True, text=True)
        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertEqual(json.loads(report.stdout)['model_groups'][0]['tokens_all_attempts']['total_tokens']['known_sum'], 30)

    def test_unknown_run_is_rejected_before_request_execution(self):
        with self.assertRaises(ValueError):
            with ModelRequest(provider='test', model='test', run_id='missing'):
                self.fail('must not enter API request body')
        self.assertFalse((log_dir() / 'run_missing.jsonl').exists())

    def test_context_attribution_and_ttft_validation(self):
        base = dict(run_id=self.run, provider='test', model='test', stage=0,
                    purpose='planning', latency_seconds=1)
        with self.assertRaises(ValueError):
            record_model_request(**base, ttft_seconds=2)
        with self.assertRaises(ValueError):
            record_model_request(**base, context_tokens={'stm_tokens': 2, 'method': 'tokenizer'})
        with self.assertRaises(ValueError):
            record_model_request(**base, usage={'input_tokens': 2},
                                 context_tokens={'stm_tokens': 3, 'method': 'provider'})
        record = record_model_request(**base, usage={'input_tokens': 0, 'output_tokens': 0})
        self.assertEqual(record['usage']['total_tokens'], 0)


if __name__ == '__main__':
    unittest.main()
