"""Host-side API telemetry. No provider SDK, tokenizer estimate, or network calls.

Wrap one API attempt with ModelRequest, or import a measured host record via CLI.
Token counts must use the normalized semantics documented in METRICS.md.
"""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time
import uuid

from experiment_log import current_run, write_event, validate_run_id, locked, _append

TOKEN_FIELDS = ('input_tokens', 'output_tokens', 'total_tokens',
                'cached_input_tokens', 'reasoning_output_tokens')
PURPOSES = ('planning', 'stm_update', 'ltm_update', 'other')


def count(value, name):
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError(f'{name} must be a non-negative integer or null')
    return value


def normalize_usage(usage=None):
    usage = {} if usage is None else usage
    if not isinstance(usage, dict) or set(usage) - set(TOKEN_FIELDS):
        raise ValueError('usage must contain only normalized token fields')
    result = {name: count(usage.get(name), name) for name in TOKEN_FIELDS}
    input_count, output_count = result['input_tokens'], result['output_tokens']
    if input_count is not None and output_count is not None:
        total = input_count + output_count
        if result['total_tokens'] is not None and result['total_tokens'] != total:
            raise ValueError('total_tokens must equal input_tokens + output_tokens')
        result['total_tokens'] = total
    for subset, parent in (('cached_input_tokens', 'input_tokens'),
                           ('reasoning_output_tokens', 'output_tokens')):
        if result[subset] is not None and result[parent] is not None and result[subset] > result[parent]:
            raise ValueError(f'{subset} is a subset of {parent}')
    return result


def validate_labels(provider, model, stage, purpose):
    if not isinstance(provider, str) or not provider.strip() or not isinstance(model, str) or not model.strip():
        raise ValueError('provider and exact model identifier are required')
    if stage is not None and (type(stage) is not int or stage not in range(6)):
        raise ValueError('stage must be 0-5, or null for unknown/mixed stages')
    if purpose not in PURPOSES:
        raise ValueError(f'purpose must be one of {PURPOSES}')


def duration(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f'{name} must be a finite non-negative number')
    return value


def context_usage(value, usage):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {'stm_tokens', 'ltm_tokens', 'method', 'tokenizer'}:
        raise ValueError('invalid context_tokens fields')
    if value.get('method') not in ('provider', 'tokenizer'):
        raise ValueError('context token attribution needs provider or tokenizer provenance')
    if value['method'] == 'tokenizer' and not value.get('tokenizer'):
        raise ValueError('tokenizer name/version is required')
    result = dict(value)
    for name in ('stm_tokens', 'ltm_tokens'):
        result[name] = count(value.get(name), name)
    known = sum(result[name] or 0 for name in ('stm_tokens', 'ltm_tokens'))
    if usage['input_tokens'] is not None and known > usage['input_tokens']:
        raise ValueError('memory context tokens exceed total request input')
    return result


def record_model_request(*, run_id, provider, model, stage, purpose,
                         latency_seconds, status='completed', usage=None,
                         ttft_seconds=None, context_tokens=None,
                         tool_call_ids=None, request_id=None, provider_request_id=None,
                         attempt=1, error_type=None, measurement_source='host_import',
                         started_at=None):
    """Append one completed attempt measured by the host, never by the LLM.

    Latency ends at response/stream completion or failure. Retries are separate
    attempts. No prompt, response body, API key, or exception message is stored.
    """
    validate_labels(provider, model, stage, purpose)
    duration(latency_seconds, 'latency_seconds')
    if status not in ('completed', 'error', 'cancelled'):
        raise ValueError('invalid request status')
    if type(attempt) is not int or attempt < 1:
        raise ValueError('attempt must be a positive integer')
    if measurement_source not in ('host_import', 'host_monotonic'):
        raise ValueError('measurement_source must identify host instrumentation')
    if ttft_seconds is not None:
        duration(ttft_seconds, 'ttft_seconds')
        if ttft_seconds > latency_seconds:
            raise ValueError('TTFT cannot exceed full response latency')
    normalized = normalize_usage(usage)
    context = context_usage(context_tokens, normalized)
    ids = tool_call_ids if tool_call_ids is not None else []
    if not isinstance(ids, list) or any(not isinstance(value, str) or not value for value in ids):
        raise ValueError('tool_call_ids must be a list of logged call_id strings')
    validate_run_id(run_id)
    event = {
        'event': 'model_request_end', 'request_id': request_id or uuid.uuid4().hex,
        'provider_request_id': provider_request_id, 'provider': provider,
        'model': model, 'stage': stage, 'purpose': purpose, 'attempt': attempt,
        'status': status, 'latency_seconds': latency_seconds,
        'ttft_seconds': ttft_seconds, 'usage': normalized,
        'usage_source': 'provider' if any(v is not None for v in normalized.values()) else None,
        'context_tokens': context, 'tool_call_ids': ids, 'error_type': error_type,
        'measurement_source': measurement_source, 'started_at': started_at,
    }
    with locked() as directory:
        path = directory / f'run_{run_id}.jsonl'
        if not path.is_file():
            raise ValueError('start the experiment before importing model metrics')
        for line in path.read_text(encoding='utf-8').splitlines():
            existing = json.loads(line)
            if existing.get('event') == 'model_request_end' and existing.get('request_id') == event['request_id']:
                raise ValueError('request_id already recorded; use a separate ID for each attempt')
        return _append(directory, run_id, event)


class ModelRequest:
    """Use inside sync or async host code around exactly one API request.

    For streaming, keep the block open until the stream is exhausted and call
    first_token() at the first content delta, not at an empty metadata event.
    """
    def __init__(self, *, provider, model, stage=None, purpose='other', run_id=None, attempt=1):
        validate_labels(provider, model, stage, purpose)
        if type(attempt) is not int or attempt < 1:
            raise ValueError('attempt must be a positive integer')
        self.labels = dict(provider=provider, model=model, stage=stage, purpose=purpose, attempt=attempt)
        self.run_id = run_id
        self.request_id = uuid.uuid4().hex
        self.usage = None
        self.context_tokens = None
        self.tool_call_ids = []
        self.provider_request_id = None
        self.ttft = None
        self.entered = False

    def __enter__(self):
        if self.entered:
            raise RuntimeError('ModelRequest instances cannot be reused')
        self.entered = True
        self.run_id = self.run_id or current_run()
        validate_run_id(self.run_id)
        with locked() as directory:
            if not (directory / f'run_{self.run_id}.jsonl').is_file():
                raise ValueError('start the experiment before measuring a model request')
        self.started_at = datetime.now(timezone.utc).isoformat()
        write_event(dict(self.labels, event='model_request_start', request_id=self.request_id,
                         measurement_source='host_monotonic'), self.run_id)
        # Exclude durable start-log I/O from the API latency interval.
        self.started = time.perf_counter()
        return self

    def first_token(self):
        if self.ttft is None:
            self.ttft = time.perf_counter() - self.started

    def set_usage(self, usage, *, context_tokens=None, provider_request_id=None):
        self.usage = normalize_usage(usage)
        self.context_tokens = context_usage(context_tokens, self.usage)
        self.provider_request_id = provider_request_id

    def __exit__(self, error_type, error, traceback):
        elapsed = time.perf_counter() - self.started
        record_model_request(
            **self.labels, run_id=self.run_id, request_id=self.request_id,
            latency_seconds=elapsed, ttft_seconds=self.ttft,
            status='completed' if error_type is None else
                   'cancelled' if error_type.__name__ in ('CancelledError', 'KeyboardInterrupt') else 'error',
            usage=self.usage, context_tokens=self.context_tokens,
            tool_call_ids=self.tool_call_ids, provider_request_id=self.provider_request_id,
            error_type=error_type.__name__ if error_type else None,
            measurement_source='host_monotonic', started_at=self.started_at)
        return False


def main():
    parser = argparse.ArgumentParser(description='Import one host-measured API attempt as JSON')
    parser.add_argument('record', type=Path, help='JSON object matching record_model_request arguments')
    args = parser.parse_args()
    record = json.loads(args.record.read_text(encoding='utf-8'))
    print(json.dumps(record_model_request(**record), ensure_ascii=True))


if __name__ == '__main__':
    main()
