"""Pure result classification shared by the live harness and offline tests."""


def motion_completed(report, component):
    # A successful zero-degree turn must not hide a failed translation.
    return any(line.startswith(f'- {component}:') and
               'başarıyla tamamlandı' in line and 'Gerçekleşen' in line
               for line in report.splitlines())


def summarize(results):
    counts = {status: sum(row['status'] == status for row in results)
              for status in ('passed', 'failed', 'skipped')}
    if counts['failed']:
        status, code = 'failed', 1
    elif counts['skipped'] or not results:
        status, code = 'incomplete', 3
    else:
        status, code = 'passed', 0
    return dict(counts, total=len(results), status=status, exit_code=code)
