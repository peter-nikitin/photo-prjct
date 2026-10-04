#!/bin/sh
set -eu
root_dir="$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)"
python3 - "$root_dir" <<'PY'
import concurrent.futures
import importlib.util
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

repository = pathlib.Path(sys.argv[1])
spec = importlib.util.spec_from_file_location('web_slot', repository / 'deploy/web-slot.py')
slot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(slot)
prefix = 'handoff-' + uuid.uuid4().hex[:10]
containers = []

def docker(*args):
    return subprocess.check_output(['docker', *args], text=True).strip()

def wait_for(check):
    deadline = time.monotonic() + 40
    while not check():
        if time.monotonic() >= deadline:
            raise AssertionError('Disposable fixture did not become ready')
        time.sleep(.2)

with tempfile.TemporaryDirectory(prefix='web-handoff-') as directory:
    root = pathlib.Path(directory)
    nginx = root / 'deploy/nginx'
    shutil.copytree(repository / 'deploy/nginx', nginx)
    (nginx / 'selected-slot').unlink(missing_ok=True)
    certificate = root / 'certificates/live/photo-prjct'
    certificate.mkdir(parents=True)
    state = root / 'state'
    state.mkdir()
    program = root / 'backend.py'
    program.write_text('''import http.server, os, pathlib, time
class Backend(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/hold':
            pathlib.Path('/state/accepted').touch()
            deadline = time.monotonic() + 30
            while not pathlib.Path('/state/release').exists() and time.monotonic() < deadline: time.sleep(.05)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(os.environ['SLOT'].encode())
    def log_message(self, *args): pass
http.server.ThreadingHTTPServer(('0.0.0.0', 8000), Backend).serve_forever()
''')
    docker('network', 'create', prefix)
    try:
        docker('run', '--rm', '--entrypoint', 'openssl', '-v', f'{certificate}:/certificates',
               'certbot/certbot:v2.11.0', 'req', '-x509', '-nodes', '-newkey', 'rsa:2048',
               '-days', '1', '-keyout', '/certificates/privkey.pem', '-out',
               '/certificates/fullchain.pem', '-subj', '/CN=findme-photo.ru')
        identities = {}
        for name in ('web', 'web-next'):
            identity = prefix + '-' + name
            containers.append(identity)
            identities[name] = identity
            docker('run', '-d', '--name', identity, '--network', prefix,
                   '--network-alias', name, '-e', f'SLOT={name}',
                   '-v', f'{program}:/backend.py:ro', '-v', f'{state}:/state',
                   '--health-cmd', "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/health/')\"",
                   '--health-interval', '1s', '--health-retries', '20',
                   '--entrypoint', 'python', os.environ.get('WEB_HANDOFF_TEST_IMAGE', 'python:3.12'), '/backend.py')
            wait_for(lambda: docker('inspect', '--format', '{{.State.Health.Status}}', identity) == 'healthy')
        edge = prefix + '-nginx'
        containers.append(edge)
        docker('run', '-d', '--name', edge, '--network', prefix,
               '-e', 'PUBLIC_DOMAIN=findme-photo.ru', '-e', 'WORKER_POOL_PRIVATE_API_IPV4=10.1.0.2',
               '-p', '127.0.0.1::443', '-p', '127.0.0.1::8443',
               '-v', f'{nginx}:/opt/nginx:ro', '-v', f'{root / "certificates"}:/etc/letsencrypt:ro',
               '--entrypoint', '/bin/sh', 'nginx:1.27-alpine', '/opt/nginx/reload-nginx.sh')
        def compose(_root, *args):
            if args[:2] == ('ps', '-q'):
                return ['docker', 'ps', '-q', '--filter', f'name=^{identities[args[2]]}$']
            assert args[:3] == ('exec', '-T', 'nginx'), args
            return ['docker', 'exec', edge, *args[3:]]
        slot.compose = compose
        port = docker('port', edge, '443/tcp').rsplit(':', 1)[1]
        private_port = docker('port', edge, '8443/tcp').rsplit(':', 1)[1]
        def request(path, port=port):
            return docker('exec', edge, 'wget', '-q', '--no-check-certificate', '-O', '-',
                          '--header', 'Host: findme-photo.ru', 'https://127.0.0.1:' + ('8443' if port == private_port else '443') + path)
        wait_for(lambda: docker('exec', edge, 'wget', '-q', '-O', '-', 'http://127.0.0.1:8080/health/') == 'web')
        assert slot.selected(root) == 'web'
        with concurrent.futures.ThreadPoolExecutor() as executor:
            held = executor.submit(request, '/hold')
            wait_for(lambda: (state / 'accepted').exists())
            slot.switch(root, 'web-next')
            assert request('/health/') == 'web-next'
            assert request('/internal/photo-processing/v1/readiness', private_port) == 'web-next'
            assert docker('exec', edge, 'wget', '-q', '-O', '-', 'http://127.0.0.1:8080/internal/photo-import/v1/readiness') == 'web-next'
            denied = subprocess.run(
                ['docker', 'exec', edge, 'wget', '-q', '--no-check-certificate', '-O', '/dev/null',
                 '--header', 'Host: findme-photo.ru',
                 'https://127.0.0.1/internal/photo-import/v1/readiness'], capture_output=True)
            assert denied.returncode != 0 and b'404' in denied.stderr
            try:
                slot.drain(root, 0)
            except ValueError as error:
                assert 'accepted requests' in str(error)
            else:
                raise AssertionError('Held request was incorrectly declared drained')
            assert not held.done()
            assert docker('inspect', '--format', '{{.State.Running}}', identities['web']) == 'true'
            (state / 'release').touch()
            assert held.result(timeout=15) == 'web'
        slot.drain(root, 15)
        docker('stop', identities['web'])
        assert request('/health/') == 'web-next'
        # A real nginx -t failure must leave the healthy selected route intact.
        template = nginx / 'https.conf.template'
        valid_template = template.read_text()
        template.write_text(valid_template + '\ninvalid_nginx_directive;\n')
        try:
            slot.switch(root, 'web-next')
        except subprocess.CalledProcessError:
            pass
        else:
            raise AssertionError('Invalid Nginx candidate succeeded')
        assert slot.selected(root) == 'web-next'
        assert request('/health/') == 'web-next'
        template.write_text(valid_template)
        # A crashed selected container loses its Docker DNS alias. Inspecting
        # the installed choice must not validate that now-unresolvable backend.
        docker('stop', identities['web-next'])
        inspection = subprocess.run(['docker', 'exec', edge, 'nginx', '-T'], capture_output=True)
        assert inspection.returncode != 0 and b'host not found in upstream' in inspection.stderr
        assert slot.installed(root) == 'web-next'
        generation = slot.workers(root)
        sys.argv = ['web-slot.py', '--root', str(root), 'reconcile']
        slot.main()
        assert slot.selected(root) == 'web-next'
        assert slot.workers(root) == generation
        slot.drain(root, 15)
        docker('start', identities['web'])
        wait_for(lambda: docker('inspect', '--format', '{{.State.Health.Status}}', identities['web']) == 'healthy')
        slot.switch(root, 'web')
        assert slot.selected(root) == 'web'
        assert request('/health/') == 'web'
        slot.drain(root, 15)
        print('Held requests drained; routes and failed reload verified; stopped selected backend reconciled without DNS and replaced by healthy slot.')
    finally:
        for identity in reversed(containers):
            subprocess.run(['docker', 'rm', '-f', identity], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(['docker', 'network', 'rm', prefix], stdout=subprocess.DEVNULL)
PY
