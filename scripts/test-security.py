import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('security', Path(__file__).with_name('install-security.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class SecurityTests(unittest.TestCase):
    def test_scoped_and_repeatable(self):
        fixture = '''server {
    root /opt/md-next/backend/app/static/fake/;
    location /vpn-secret/cdn-get { proxy_pass http://127.0.0.1:8447; }
    location /vpn-secret { proxy_pass http://127.0.0.1:8446; }
    location / {
        try_files $uri $uri/ =404;
    }
}
server {
    location /api/ { proxy_pass http://127.0.0.1:8000/api/; }
    location /sub/ { proxy_pass http://127.0.0.1:8000/api/v1/sub/; }
}'''
        result = module.transform(fixture)
        self.assertEqual(module.transform(result), result)
        for route in ('/vpn-secret/cdn-get', '/vpn-secret', '/sub/'):
            line = next(line for line in fixture.splitlines() if 'location '+route+' ' in line)
            self.assertIn(line, result)
        self.assertEqual(result.count('if ($md_panel_banned)'), 1)
        self.assertIn('X-Forwarded-For $remote_addr', result)

    def test_unknown_layout_refused(self):
        with self.assertRaises(ValueError):
            module.transform('server { location / { return 200; } }')

if __name__ == '__main__':
    unittest.main()
