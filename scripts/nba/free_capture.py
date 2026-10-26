"""Encrypted append-only public NBA source evidence, never a prediction ledger.

No paid endpoints, API keys, authenticated requests or publication. Source fetch
success is not semantic validation. Every fetch and failure gets an observation.
"""
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener
import argparse
import json
import uuid
from cryptography.fernet import Fernet

VERSION = 'nba-raw-v1'
MAX_BYTES = 8 * 1024 * 1024
SOURCES = [
    ('nba-schedule', 'schedule', 'https://www.nba.com/schedule'),
    ('nba-official-index', 'report_index', 'https://official.nba.com/'),
    ('nba-injury-current-season', 'injury_report',
     'https://official.nba.com/nba-injury-report-2026-27-season/'),
    ('nba-news-index', 'availability_index', 'https://www.nba.com/news'),
]


def now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def safe_url(url):
    p = urlsplit(url)
    if (p.scheme != 'https' or not p.hostname or p.username or p.password
            or p.port not in (None, 443) or p.query or p.fragment
            or not (p.hostname == 'nba.com' or p.hostname.endswith('.nba.com'))):
        raise ValueError('Only unauthenticated HTTPS NBA URLs without queries allowed')
    return url


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url):
    safe_url(url)
    req = Request(url, headers={'User-Agent': 'OLS-NBA-Evidence/0.1',
                                'Accept': 'text/html,application/pdf,application/json'})
    opener = build_opener(SafeRedirect())
    try:
        response = opener.open(req, timeout=30)
    except HTTPError as exc:
        response = exc
    with response:
        body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError('Response exceeds capture size limit')
        return response.status, dict(response.headers), body, safe_url(response.url)


def outside_git(path):
    path = Path(path).resolve()
    for parent in (path, *path.parents):
        if (parent / '.git').exists():
            raise ValueError('Evidence and encryption key must remain outside Git')
    return path


def exclusive(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as f:
        f.write(content)
        f.flush()
        import os
        os.fsync(f.fileno())


def key_for(path):
    path = outside_git(path)
    if not path.exists():
        try:
            exclusive(path, Fernet.generate_key())
        except FileExistsError:
            pass
    return Fernet(path.read_bytes())


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.current = [dict(attrs).get('href', ''), '']

    def handle_data(self, data):
        if self.current is not None:
            self.current[1] += data

    def handle_endtag(self, tag):
        if tag == 'a' and self.current is not None:
            self.links.append(self.current)
            self.current = None


def evidence_links(body, base):
    parser = Links()
    parser.feed(body.decode('utf-8', errors='replace'))
    out = []
    for href, label in parser.links:
        url = urljoin(base, href)
        text = (href + ' ' + label).lower()
        if not any(word in text for word in ('injury', 'availability', 'out for',
                                            'ruled out', 'minutes restriction')):
            continue
        try:
            safe_url(url)
        except ValueError:
            continue
        if url not in out:
            out.append(url)
    return out


def observe(root, cipher, source, run_id, ordinal, transport=fetch):
    source_id, kind, url = source
    safe_url(url)
    record = {'schema': VERSION, 'observation_id': uuid.uuid4().hex,
              'run_id': run_id, 'ordinal': ordinal, 'source_id': source_id,
              'kind': kind, 'url': url, 'request_started_at': now(),
              'semantic_coverage': 'UNVERIFIED', 'status': None,
              'provider_last_modified': None, 'header_date': None,
              'error_code': None, 'links': []}
    try:
        status, headers, body, final_url = transport(url)
        safe_url(final_url)
        if not isinstance(body, bytes) or len(body) > MAX_BYTES:
            raise ValueError('Invalid response body')
        record['observed_at'] = now()
        h = {k.lower(): v for k, v in headers.items()}
        encrypted = cipher.encrypt(body)
        rel = 'objects/' + record['observation_id'] + '.fernet'
        exclusive(root / rel, encrypted)
        record.update(status=status, final_url=final_url,
                      provider_last_modified=h.get('last-modified'),
                      header_date=h.get('date'), content_type=h.get('content-type'),
                      raw_sha256=sha256(body).hexdigest(), raw_bytes=len(body),
                      encrypted_sha256=sha256(encrypted).hexdigest(),
                      encrypted_bytes=len(encrypted), object_path=rel)
        if not 200 <= status < 300:
            record['error_code'] = 'HTTP_NON_SUCCESS'
        elif 'text/html' in h.get('content-type', ''):
            record['links'] = evidence_links(body, final_url)
    except Exception as exc:
        record['observed_at'] = now()
        # Never serialize exception text; it may contain request secrets or paths.
        record['error_code'] = type(exc).__name__
    rel_record = 'observations/' + record['observation_id'] + '.json'
    exclusive(root / rel_record,
              (json.dumps(record, sort_keys=True, indent=2) + '\n').encode())
    return record, rel_record


def verify(root, key_file):
    root = outside_git(root)
    cipher = Fernet(outside_git(key_file).read_bytes())
    manifests = sorted((root / 'runs').glob('*.json'))
    count = failures = 0
    records_seen, objects_seen = set(), set()
    for manifest in manifests:
        run = json.loads(manifest.read_text())
        if run.get('schema') != VERSION:
            raise ValueError('Unknown run schema')
        for rel, expected in run['observations'].items():
            path = (root / rel).resolve()
            if not path.is_relative_to(root):
                raise ValueError('Invalid observation path')
            raw = path.read_bytes()
            if path in records_seen:
                raise ValueError('Observation referenced by multiple runs')
            records_seen.add(path)
            if sha256(raw).hexdigest() != expected:
                raise ValueError('Observation hash mismatch')
            record = json.loads(raw)
            if record.get('run_id') != run['run_id'] or record.get('schema') != VERSION:
                raise ValueError('Observation/run binding mismatch')
            if record.get('object_path'):
                obj = (root / record['object_path']).resolve()
                if not obj.is_relative_to(root):
                    raise ValueError('Invalid object path')
                encrypted = obj.read_bytes()
                objects_seen.add(obj)
                if (sha256(encrypted).hexdigest() != record['encrypted_sha256']
                        or len(encrypted) != record['encrypted_bytes']):
                    raise ValueError('Encrypted hash/size mismatch')
                plain = cipher.decrypt(encrypted)
                if (sha256(plain).hexdigest() != record['raw_sha256']
                        or len(plain) != record['raw_bytes']):
                    raise ValueError('Raw hash/size mismatch')
            count += 1
            failures += bool(record['error_code'])
    if records_seen != {p.resolve() for p in (root / 'observations').glob('*.json')}:
        raise ValueError('Unmanifested observation: incomplete run requires recovery')
    if objects_seen != {p.resolve() for p in (root / 'objects').glob('*.fernet')}:
        raise ValueError('Unmanifested object: incomplete run requires recovery')
    return {'runs': len(manifests), 'observations': count, 'fetch_failures': failures}


def capture(root, key_file, sources=SOURCES, transport=fetch, discovery_limit=12):
    root = outside_git(root)
    if (not Path(key_file).exists() and (root / 'objects').exists()
            and any((root / 'objects').glob('*.fernet'))):
        raise ValueError('Encryption key missing for retained evidence; restore the original key')
    cipher = key_for(key_file)
    run_id = uuid.uuid4().hex
    manifest = {'schema': VERSION, 'run_id': run_id, 'started_at': now(),
                'observations': {}, 'prediction_ledger': False}
    pending = list(sources)
    seen = set()
    failures = 0
    discovered = 0
    for ordinal, source in enumerate(pending):
        seen.add(source[2])
        record, rel = observe(root, cipher, source, run_id, ordinal, transport)
        manifest['observations'][rel] = sha256((root / rel).read_bytes()).hexdigest()
        failures += bool(record['error_code'])
        for url in record['links']:
            if url not in seen and not any(s[2] == url for s in pending):
                if discovered < discovery_limit:
                    pending.append(('discovered-' + sha256(url.encode()).hexdigest()[:16],
                                    'discovered_availability', url))
                    discovered += 1
    manifest.update(completed_at=now(), fetch_failures=failures,
                    configured_sources=len(sources), discovered_sources=discovered)
    exclusive(root / 'runs' / (run_id + '.json'),
              (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode())
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--key-file', type=Path, required=True)
    p.add_argument('--verify', action='store_true')
    p.add_argument('--source-config', type=Path)
    args = p.parse_args()
    if args.verify:
        result = verify(args.root, args.key_file)
    else:
        sources = SOURCES
        if args.source_config:
            config = json.loads(args.source_config.read_text(encoding='utf-8-sig'))
            sources = [(s['id'], s['kind'], s['url']) for s in config['sources']]
        result = capture(args.root, args.key_file, sources)
        result = {'run_id': result['run_id'],
                  'observations': len(result['observations']),
                  'fetch_failures': result['fetch_failures'],
                  'semantic_coverage': 'UNVERIFIED'}
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
