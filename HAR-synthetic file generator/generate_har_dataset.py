"""Generate 30 controlled HAR fixtures and a separate answer file.

Standard-library Python only. No network calls or sanitizer execution.
These small synthetic cases are development examples, not a complete benchmark.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode

OUTPUT_DIR = Path("/Users/maxnguyen/Desktop/HAR Sanitizer/HAR-synthetic file generator/output")


def make_har():
    """Return a fresh synthetic HTTP transaction with no secret yet."""
    response_text = json.dumps({"error": "Account temporarily locked"})
    return {
        "log": {
            "version": "1.2",
            "creator": {"name": "Synthetic HAR Lab", "version": "1.0"},
            "entries": [{
                "startedDateTime": "2026-09-12T10:00:00.000Z",
                "time": 50,
                "request": {
                    "method": "GET",
                    "url": "https://example.test/api/account",
                    "httpVersion": "HTTP/1.1",
                    "cookies": [],
                    "headers": [],
                    "queryString": [],
                    "headersSize": -1,
                    "bodySize": 0,
                },
                "response": {
                    "status": 403,
                    "statusText": "Forbidden",
                    "httpVersion": "HTTP/1.1",
                    "cookies": [],
                    "headers": [{"name": "Content-Type", "value": "application/json"}],
                    "content": {
                        "size": len(response_text.encode("utf-8")),
                        "mimeType": "application/json",
                        "text": response_text,
                    },
                    "redirectURL": "",
                    "headersSize": -1,
                    "bodySize": len(response_text.encode("utf-8")),
                },
                "cache": {},
                "timings": {"send": 5, "wait": 35, "receive": 10},
            }],
        }
    }


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def b64(text):
    return base64.b64encode(text.encode()).decode()


def make_cases():
    cases = []

    def add(name, edit, secrets=True, preserve=None):
        har = make_har()
        # Deterministic synthetic opaque value, with no TEST_SECRET signature.
        secret = hashlib.sha256(('fixture:' + name).encode()).hexdigest()[:32]
        entry = har['log']['entries'][0]
        edit(entry, secret)
        checks = {
            '/log/entries/0/response/status': 403,
            '/log/entries/0/time': 50,
            '/log/entries/0/timings': {'send': 5, 'wait': 35, 'receive': 10},
        }
        # Semantic body comparisons are handled separately from JSON pointers.
        content = entry['response']['content']
        decoded = base64.b64decode(content['text']).decode() if content.get('encoding') == 'base64' else content['text']
        if 'Account temporarily locked' in decoded:
            checks['response_contains'] = 'Account temporarily locked'
        checks.update(preserve or {})
        cases.append({'name': name, 'har': har, 'secrets': [secret] if secrets else [], 'preserve': checks})

    def header(e, key, value, side='request'):
        e[side]['headers'].append({'name': key, 'value': value})

    def query(e, key, value):
        e['request']['queryString'] = [{'name': key, 'value': value}]
        e['request']['url'] += '?' + urlencode({key: value})

    def request_body(e, text, mime='application/json', params=None):
        e['request']['method'] = 'POST'
        e['request']['postData'] = {'mimeType': mime, 'text': text}
        if params is not None:
            e['request']['postData']['params'] = params
        e['request']['bodySize'] = len(text.encode())
        header(e, 'Content-Type', mime)

    def response_body(e, text, mime='application/json', encoded=False):
        content = {'size': len(text.encode()), 'mimeType': mime, 'text': b64(text) if encoded else text}
        if encoded:
            content['encoding'] = 'base64'
        e['response']['content'] = content
        e['response']['headers'][0]['value'] = mime
        e['response']['bodySize'] = len(text.encode())

    for label, name in (('title_case', 'Authorization'), ('lower_case', 'authorization'), ('mixed_case', 'aUtHoRiZaTiOn')):
        add('header_' + label, lambda e, s, key=name: header(e, key, 'Bearer ' + s))
    for name in ('access_token', 'api_key', 'refresh_token'):
        add('query_' + name, lambda e, s, key=name: query(e, key, s))
    add('query_encoded_password', lambda e, s: query(e, 'password', 'prefix +/=' + s))
    add('request_cookie', lambda e, s: (
        e['request']['cookies'].append({'name': 'sessionid', 'value': s}),
        header(e, 'Cookie', 'sessionid=' + s)))
    add('response_cookie', lambda e, s: (
        e['response']['cookies'].append({'name': 'sid', 'value': s}),
        header(e, 'Set-Cookie', 'sid=' + s + '; HttpOnly', 'response')))
    add('form_text', lambda e, s: request_body(e, urlencode({'password': s}), 'application/x-www-form-urlencoded'))
    add('form_params_and_text', lambda e, s: request_body(e, urlencode({'password': s}),
        'application/x-www-form-urlencoded', [{'name': 'password', 'value': s}]))
    add('nested_request_json', lambda e, s: request_body(e, json.dumps({'account': {'password': s}})))
    add('array_request_json', lambda e, s: request_body(e, json.dumps({'items': [{'access_token': s}]})))
    add('nested_response_json', lambda e, s: response_body(e, json.dumps({'account': {'password': s}, 'error': 'Account temporarily locked'})))
    add('base64_response_json', lambda e, s: response_body(e, json.dumps({'access_token': s, 'error': 'Account temporarily locked'}), encoded=True))
    add('credential_echo', lambda e, s: (header(e, 'Authorization', 'Bearer ' + s),
        response_body(e, json.dumps({'debug_echo': s, 'error': 'Account temporarily locked'}))))
    add('jwt_claim', lambda e, s: header(e, 'X-Session',
        b64(json.dumps({'alg': 'HS256'})).rstrip('=') + '.' +
        b64(json.dumps({'private_claim': s})).rstrip('=') + '.' + b64('fake-signature').rstrip('=')))
    add('url_password', lambda e, s: e['request'].update(url='https://demo:' + s + '@example.test/api/account'))
    add('xml_password', lambda e, s: request_body(e, '<account><password>' + s + '</password></account>', 'application/xml'))
    add('html_password', lambda e, s: response_body(e, '<input name="password" value="' + s + '">', 'text/html'))
    add('unknown_opaque_field', lambda e, s: request_body(e, json.dumps({'ticket': s})))
    add('escaped_json_password', lambda e, s: request_body(e, json.dumps({'password': '雪"\\' + s})))
    add('basic_auth', lambda e, s: header(e, 'Authorization', 'Basic ' + b64('demo:' + s)))
    add('custom_api_key_header', lambda e, s: header(e, 'X-Api-Key', s, 'response'))
    add('clean_query_code', lambda e, s: query(e, 'code', 'HTTP404'), False,
        {'/log/entries/0/request/queryString/0/value': 'HTTP404'})
    add('clean_query_state', lambda e, s: query(e, 'state', 'ready'), False,
        {'/log/entries/0/request/queryString/0/value': 'ready'})
    add('clean_javascript', lambda e, s: response_body(e, 'console.error("Account temporarily locked");', 'application/javascript'), False)
    add('clean_html', lambda e, s: response_body(e, '<p>Account temporarily locked</p>', 'text/html'), False)
    add('clean_password_policy', lambda e, s: request_body(e, json.dumps({'password_policy': 'minimum_12_chars'})), False,
        {'request_json': {'password_policy': 'minimum_12_chars'}})
    add('clean_base', lambda e, s: None, False)
    assert len(cases) == 30
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    output = args.output_dir.expanduser()
    output.mkdir(parents=True, exist_ok=True)
    cases = make_cases()
    answers = []
    for case in cases:
        filename = case["name"] + ".har"
        write_json(output / filename, case["har"])
        answers.append({
            "file": filename,
            "secrets": case["secrets"],
            "preserve": case["preserve"],
        })
    write_json(output / "ground_truth.json", {
        "description": "Expected outcomes for synthetic fixtures; keep separate from sanitizer inputs.",
        "cases": answers,
    })
    print(f"Created {len(cases)} HAR files and ground_truth.json in:\n{output}")


if __name__ == "__main__":
    main()
