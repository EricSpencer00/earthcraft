"""Persist safe HF retry metadata; never retry a mutation inside the uploader."""
from datetime import timezone
from email.utils import parsedate_to_datetime
import json
import math
from pathlib import Path
import re
import time

SAFE_HEADERS = {'date', 'retry-after', 'ratelimit', 'ratelimit-policy'}


def _http_date(value):
    try:
        date = parsedate_to_datetime(value)
        return date.replace(tzinfo=timezone.utc).timestamp() if date.tzinfo is None else date.timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def retry_receipt(error, *, now=None, attempt_id=None, archive_sha256=None):
    """Return a cooldown for 429/conflict/transient errors, or None to stop."""
    now = time.time() if now is None else now
    response = getattr(error, 'response', None)
    status = getattr(response, 'status_code', None)
    headers = {str(k).lower(): str(v) for k, v in getattr(response, 'headers', {}).items()
               if str(k).lower() in SAFE_HEADERS}
    message = str(error)
    if status == 429:
        repo_limit = bool(re.search(r'repository commits|repo commits', message, re.I))
        # An unclassified commit-endpoint 429 is conservatively an hourly action limit.
        repo_limit |= '/commit/' in str(getattr(response, 'url', ''))
        waits = [3600 if repo_limit else 300]
        retry_after = headers.get('retry-after', '')
        if re.fullmatch(r'\d+(?:\.\d+)?', retry_after):
            waits.append(float(retry_after))
        elif retry_after:
            retry_date = _http_date(retry_after)
            server_date = _http_date(headers.get('date'))
            if retry_date is not None:
                # Respect absolute dates and compensate for a slow local clock.
                waits.append(max(0, retry_date - now, retry_date - (server_date or now)))
        waits.extend(float(v) for v in re.findall(r'(?:^|;)\s*t\s*=\s*(\d+)', headers.get('ratelimit', '')))
        for amount, unit in re.findall(r'(?:retry after|retry this action in(?: about)?)\s+(\d+(?:\.\d+)?)\s*(seconds?|minutes?|hours?)', message, re.I):
            waits.append(float(amount) * (3600 if unit.lower().startswith('hour') else 60 if unit.lower().startswith('minute') else 1))
        wait = max(waits)
        kind = 'repository_commit_limit' if repo_limit else 'api_rate_limit'
        safety = 30 if repo_limit else 5
    elif status in (409, 412) or getattr(error, 'concurrent_index_change', False):
        kind, wait, safety = 'concurrent_index_change', 60, 0
    elif status in (408, 425, 500, 502, 503, 504) or isinstance(error, (TimeoutError, ConnectionError)):
        kind, wait, safety = 'transient_or_unknown_outcome', 60, 0
    else:
        try:
            import httpx
            transient = isinstance(error, httpx.TransportError)
        except ImportError:
            transient = False
        if not transient:
            return None
        kind, wait, safety = 'transient_or_unknown_outcome', 60, 0
    return {'schema': 'earthcraft.hf-retry.v1', 'kind': kind, 'status_code': status,
            'observed_at': now, 'not_before': math.ceil(now + wait + safety),
            'wait_seconds': wait, 'safety_seconds': safety, 'headers': headers,
            'attempt_id': attempt_id, 'archive_sha256': archive_sha256}


def persist_retry(path, receipt):
    path = Path(path)
    partial = path.with_name(path.name + '.partial')
    with partial.open('w') as stream:
        json.dump(receipt, stream, sort_keys=True)
        stream.write('\n')
        stream.flush()
        import os
        os.fsync(stream.fileno())
    partial.replace(path)
