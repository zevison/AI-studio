import json
from pathlib import Path
import tempfile
import unittest
from core.hypit_cli_guard import validate_project_boundary

class CompilerBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        (self.root/'package.json').write_text(json.dumps({'hypit':{'project':True}}),encoding='utf8')

    def source(self,text): (self.root/'main.svml').write_text(text,encoding='utf8')

    def test_official_module_accepted(self):
        self.source('<import as="media" from="@hypit/media@1"/>')
        validate_project_boundary(self.root)

    def test_compiler_cannot_read_secrets_or_fetch_remote_content(self):
        for source in ('<media:Video src="../secrets.mp4"/>','<media:Video src="https://example.com/v.mp4"/>',
                       '<media:Video src="&#46;&#46;/secrets.mp4"/>', '<import from="@evil/plugin@1"/>',
                       '<import from="@hypit/fake-module@1"/>'):
            with self.subTest(source=source),self.assertRaises(PermissionError):
                self.source(source); validate_project_boundary(self.root)

    def test_project_package_activation_refused(self):
        self.source('<svml/>')
        (self.root/'package.json').write_text(json.dumps({'hypit':{'project':True},
            'dependencies':{'evil':'1'}}),encoding='utf8')
        with self.assertRaises(PermissionError): validate_project_boundary(self.root)

    def test_css_external_resources_refused(self):
        self.source('<svml/>')
        for css in ('background: url(https://example.com)', '@import "secret";', 'background: u\\72l(secret)'):
            (self.root/'style.svs').write_text(css,encoding='utf8')
            with self.subTest(css=css),self.assertRaises(PermissionError): validate_project_boundary(self.root)
