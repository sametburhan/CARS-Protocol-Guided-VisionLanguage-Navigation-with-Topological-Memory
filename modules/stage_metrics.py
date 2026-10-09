"""MCP-observed stage intervals, explicitly distinct from API latency."""
import json
from pathlib import Path
import time
import uuid

from experiment_log import locked, _active, _append, validate_run_id
from model_metrics import validate_labels


def start_stage(stage, provider, model):
    validate_labels(provider, model, stage, 'planning')
    if stage is None:
        raise ValueError('an explicit stage 0-5 is required')
    with locked() as directory:
        run_id = _active(directory)
        stage_id = run_id + ':' + uuid.uuid4().hex
        _append(directory, run_id, {
            'event': 'cognitive_stage_start', 'stage_id': stage_id,
            'stage': stage, 'provider': provider, 'model': model,
            'label_source': 'caller_supplied',
            'clock_boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'monotonic_started': time.monotonic(),
        })
        return stage_id


def finish_stage(stage_id, outcome):
    if outcome not in ('completed', 'failed', 'cancelled'):
        raise ValueError('outcome must be completed, failed, or cancelled')
    run_id, separator, identifier = stage_id.partition(':')
    validate_run_id(run_id)
    if not separator or len(identifier) != 32 or any(c not in '0123456789abcdef' for c in identifier):
        raise ValueError('invalid stage_id; use the value returned by start_cognitive_stage')
    with locked() as directory:
        path = directory / f'run_{run_id}.jsonl'
        rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        matching = [r for r in rows if r.get('stage_id') == stage_id]
        if not matching or matching[0]['event'] != 'cognitive_stage_start':
            raise ValueError('unknown stage_id')
        if any(r['event'] == 'cognitive_stage_end' for r in matching):
            raise ValueError('stage already finished')
        start = matching[0]
        same_boot = start['clock_boot_id'] == Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        elapsed = time.monotonic() - start['monotonic_started'] if same_boot else None
        return _append(directory, run_id, {
            'event': 'cognitive_stage_end', 'stage_id': stage_id,
            'stage': start['stage'], 'provider': start['provider'], 'model': start['model'],
            'label_source': start['label_source'], 'outcome': outcome,
            'observed_stage_seconds': elapsed,
            'timing_valid': same_boot,
            'measurement_scope': 'mcp_stage_wall_time_including_tools_and_host_waits',
        })
