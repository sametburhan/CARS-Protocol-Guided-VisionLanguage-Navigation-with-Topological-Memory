"""Read-only per-run, per-model/stage API and memory telemetry summaries."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics

from model_metrics import TOKEN_FIELDS


def distribution(values):
    if not values:
        return {'samples': 0, 'mean': None, 'p50': None, 'p95': None}
    ordered = sorted(values)
    # Nearest-rank p95, including the single-sample case.
    import math
    return {'samples': len(values), 'mean': statistics.mean(values),
            'p50': statistics.median(values), 'p95': ordered[math.ceil(.95 * len(values)) - 1]}


def coverage(values):
    known = [value for value in values if value is not None]
    return {'known_sum': sum(known) if known else None,
            'reported_requests': len(known), 'missing_requests': len(values) - len(known)}


def summarize(records):
    runs = {}
    groups = defaultdict(list)
    memory = defaultdict(lambda: {'calls': 0, 'returned': 0, 'exceptions': 0,
                                   'input_characters': 0, 'input_utf8_bytes': 0,
                                   'output_characters': 0, 'output_utf8_bytes': 0})
    seen = set()
    starts, ends = set(), set()
    stage_starts, stage_ends = set(), set()
    stage_groups = defaultdict(list)
    for row in records:
        key = (row['run_id'], row['seq'])
        if key in seen:
            raise ValueError(f'duplicate event {key}; input logs overlap')
        seen.add(key)
        if row['event'] == 'run_started':
            runs[row['run_id']] = row.get('metadata', {})
        if row['event'] == 'model_request_start':
            starts.add((row['run_id'], row['request_id']))
        if row['event'] == 'cognitive_stage_start':
            stage_starts.add((row['run_id'], row['stage_id']))
        if row['event'] == 'cognitive_stage_end':
            stage_ends.add((row['run_id'], row['stage_id']))
            stage_groups[(row['run_id'], row['provider'], row['model'], row['stage'])].append(row)
        if row['event'] == 'model_request_end':
            request_key = (row['run_id'], row['request_id'])
            if request_key in ends:
                raise ValueError(f'duplicate model request {request_key}')
            ends.add(request_key)
            groups[(row['run_id'], row['provider'], row['model'], row['stage'], row['purpose'])].append(row)
        metric = row.get('memory_metrics')
        if metric:
            item = memory[(row['run_id'], metric['store'], metric['operation'])]
            if row['event'] == 'tool_start':
                item['calls'] += 1
                for name in ('characters', 'utf8_bytes'):
                    item['input_' + name] += metric['payload'][name]
            elif row['event'] == 'tool_end':
                item['returned' if row['status'] == 'returned' else 'exceptions'] += 1
                for name in ('characters', 'utf8_bytes'):
                    item['output_' + name] += metric['payload'][name]
    result = []
    for (run_id, provider, model, stage, purpose), rows in groups.items():
        completed = [r for r in rows if r['status'] == 'completed']
        result.append({
            'run_id': run_id, 'provider': provider, 'model': model, 'stage': stage,
            'purpose': purpose, 'attempts': len(rows), 'completed': len(completed),
            'errors': sum(r['status'] == 'error' for r in rows),
            'cancelled': sum(r['status'] == 'cancelled' for r in rows),
            'completed_latency_seconds': distribution([r['latency_seconds'] for r in completed]),
            'all_attempt_latency_seconds': distribution([r['latency_seconds'] for r in rows]),
            'completed_ttft_seconds': distribution([r['ttft_seconds'] for r in completed if r['ttft_seconds'] is not None]),
            'tokens_all_attempts': {name: coverage([r['usage'][name] for r in rows]) for name in TOKEN_FIELDS},
            'memory_context_tokens': {name: coverage([(r.get('context_tokens') or {}).get(name) for r in rows])
                                      for name in ('stm_tokens', 'ltm_tokens')},
        })
    return {'runs': runs, 'model_groups': result,
            'observed_stage_groups': [
                {'run_id': key[0], 'provider': key[1], 'model': key[2], 'stage': key[3],
                 'intervals': len(rows), 'completed': sum(r['outcome'] == 'completed' for r in rows),
                 'failed': sum(r['outcome'] == 'failed' for r in rows),
                 'cancelled': sum(r['outcome'] == 'cancelled' for r in rows),
                 'invalid_timings': sum(r['observed_stage_seconds'] is None for r in rows),
                 'completed_stage_seconds': distribution([r['observed_stage_seconds'] for r in rows
                     if r['outcome'] == 'completed' and r['observed_stage_seconds'] is not None]),
                 'measurement_scope': 'includes_tools_host_waits_and_instrumentation'}
                for key, rows in stage_groups.items()],
            'unfinished_stages': [{'run_id': r, 'stage_id': s} for r, s in sorted(stage_starts - stage_ends)],
            'unfinished_model_requests': [{'run_id': r, 'request_id': q} for r, q in sorted(starts - ends)],
            'memory_payloads': [dict(values, run_id=key[0], store=key[1], operation=key[2])
                                for key, values in memory.items()],
            'notes': ['Payload characters/bytes are not tokens or full API context size.',
                      'Token sums include retries; cached/reasoning counts are subsets, not extra tokens.',
                      'Missing usage remains null; compare coverage before ranking models.',
                      'Stage null means unknown/mixed; request timing excludes intervening tool execution.',
                      'Context attribution may use different tokenizers; inspect request provenance.',
                      'Compare equivalent scenarios, outcomes, protocol variants and initial memory.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('logs', nargs='+', type=Path)
    args = parser.parse_args()
    records = []
    for path in args.logs:
        for line_number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            if line.strip():
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as error:
                    raise ValueError(f'{path}:{line_number}: incomplete or invalid JSONL') from error
    print(json.dumps(summarize(records), indent=2, ensure_ascii=True))


if __name__ == '__main__':
    main()
