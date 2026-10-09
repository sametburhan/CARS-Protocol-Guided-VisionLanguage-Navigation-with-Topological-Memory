"""Process-safe experiment traces for the Ubuntu/WSL MCP servers.

Every call has start/end events pinned to the run active at call entry.
An end status of 'returned' means the tool returned, not that motion succeeded.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
from functools import wraps
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import re
import time
import uuid


def log_dir():
    return Path(os.environ.get('CARS_LOG_DIR', Path(__file__).resolve().parent.parent / 'logs'))


def validate_run_id(value):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}', value):
        raise ValueError('run-id must be 1-96 letters, digits, underscores or hyphens')
    return value


@contextmanager
def locked():
    directory = log_dir()
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / '.experiment.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield directory


def atomic_text(path, value):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('w', encoding='utf-8') as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def safe_value(value):
    if getattr(value, 'type', None) == 'image':
        data = value.data
        return {'type': 'image', 'mimeType': value.mimeType,
                'base64_length': len(data),
                'sha256_base64': hashlib.sha256(data.encode()).hexdigest()}
    if hasattr(value, 'model_dump'):
        return safe_value(value.model_dump())
    if isinstance(value, dict):
        return {str(k): safe_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe_value(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _append(directory, run_id, event):
    sequence_path = directory / f'run_{run_id}.seq'
    sequence = int(sequence_path.read_text()) + 1 if sequence_path.exists() else 1
    record = dict(event, schema_version=2, run_id=run_id, seq=sequence,
                  timestamp=datetime.now(timezone.utc).isoformat(), pid=os.getpid())
    line = json.dumps(safe_value(record), ensure_ascii=False, allow_nan=False) + '\n'
    # Reserve before append: a crash can leave a gap, never a duplicate sequence.
    atomic_text(sequence_path, str(sequence))
    with (directory / f'run_{run_id}.jsonl').open('a', encoding='utf-8') as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())
    return record


def _start(directory, run_id=None, metadata=None):
    run_id = validate_run_id(run_id) if run_id is not None else (
        datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '_' + uuid.uuid4().hex[:12])
    # Explicit IDs never silently append to an earlier experiment.
    with (directory / f'run_{run_id}.jsonl').open('x', encoding='utf-8'):
        pass
    _append(directory, run_id, {'event': 'run_started', 'metadata': metadata or {}})
    atomic_text(directory / 'current_run.txt', run_id + '\n')
    return run_id


def start_run(run_id=None, metadata=None):
    with locked() as directory:
        return _start(directory, run_id, metadata)


def _active(directory):
    marker = directory / 'current_run.txt'
    if not marker.exists():
        return _start(directory, metadata={'automatic': True})
    return validate_run_id(marker.read_text(encoding='utf-8').strip())


def current_run():
    with locked() as directory:
        return _active(directory)


def write_event(event, run_id=None):
    with locked() as directory:
        return _append(directory, validate_run_id(run_id) if run_id else _active(directory), event)


MEMORY_TOOLS = {
    'save_long_term_memory': ('ltm', 'write'),
    'record_beyond_passage_observation': ('ltm', 'write'),
    'read_long_term_memory': ('ltm', 'read'),
    'update_short_term_memory': ('stm', 'write'),
    'read_short_term_memory': ('stm', 'read'),
}


def memory_metrics(tool, payload):
    if tool not in MEMORY_TOOLS:
        return None
    text = payload if isinstance(payload, str) else json.dumps(
        safe_value(payload), ensure_ascii=False, separators=(',', ':'))
    store, operation = MEMORY_TOOLS[tool]
    return {'store': store, 'operation': operation,
            'payload': {'characters': len(text), 'utf8_bytes': len(text.encode('utf-8'))},
            'tokens': None, 'token_source': 'unavailable_at_mcp_boundary'}


def logged_tool(server):
    """Apply BELOW @mcp.tool() so FastMCP keeps the original tool signature."""
    def decorate(function):
        signature = inspect.signature(function)

        def begin(args, kwargs):
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            return write_event({'event': 'tool_start', 'call_id': uuid.uuid4().hex,
                                'server': server, 'tool': function.__name__,
                                'inputs': safe_value(bound.arguments),
                                'memory_metrics': memory_metrics(function.__name__, bound.arguments)})

        def finish(start, began, result=None, error=None):
            write_event({'event': 'tool_end', 'call_id': start['call_id'],
                         'server': server, 'tool': function.__name__,
                         'status': 'exception' if error is not None else 'returned',
                         'output_summary': safe_value(result),
                         'memory_metrics': memory_metrics(function.__name__, result if error is None else ''),
                         'error': {'type': type(error).__name__, 'message': str(error)} if error is not None else None,
                         'duration_seconds': round(time.monotonic() - began, 6)}, start['run_id'])

        @wraps(function)
        def synchronous(*args, **kwargs):
            began = time.monotonic()
            start = begin(args, kwargs)
            try:
                result = function(*args, **kwargs)
            except BaseException as error:
                finish(start, began, error=error)
                raise
            finish(start, began, result=result)
            return result

        @wraps(function)
        async def asynchronous(*args, **kwargs):
            began = time.monotonic()
            start = begin(args, kwargs)
            try:
                result = await function(*args, **kwargs)
            except BaseException as error:
                finish(start, began, error=error)
                raise
            finish(start, began, result=result)
            return result

        return asynchronous if inspect.iscoroutinefunction(function) else synchronous
    return decorate


def main():
    parser = argparse.ArgumentParser(description='Start a fresh shared CARS experiment')
    parser.add_argument('--run-id')
    parser.add_argument('--metadata', default='{}', help='JSON object with scenario, model, pose, target, etc.')
    args = parser.parse_args()
    metadata = json.loads(args.metadata)
    if not isinstance(metadata, dict):
        parser.error('--metadata must be a JSON object')
    print(start_run(args.run_id, metadata))


if __name__ == '__main__':
    main()
