import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'integrations/moyai'))
from documentation_review import MODEL, assess_passages, prose_passages, reference_lines, review_patch
from repository_ledger import RepositoryError


class DocumentationReviewTests(unittest.IsolatedAsyncioTestCase):
    path = 'tests/compat.rs'
    refs = {'src/lib.rs:1': '#[deprecated(note = "use current")]'}
    def decide(self, line, refs, **assessment):
        patch = self.diff(line)
        item = {'passage':'C1','source':'src/lib.rs:1','source_fact':'The API is deprecated.',
                'supported':True,'correction':'', **assessment}
        return assess_passages({'assessments':[item]}, prose_passages(patch), refs, patch)

    def diff(self, line):
        return f'--- /dev/null\n+++ b/{self.path}\n@@ -0,0 +1 @@\n+//! {line}\n'

    def test_sources_exclude_new_test_prose_and_changed_policy(self):
        original = {'docs/api.md':b'Original policy\nRemoved text\n'}
        candidate = {'docs/api.md':b'New policy\nOriginal policy\n', self.path:b'//! Self citation\n',
                     'src/lib.rs':b'/// New promise\npub fn current() {}\n'}
        self.assertEqual(reference_lines(original, candidate),
                         {'docs/api.md:2':'Original policy', 'src/lib.rs:2':'pub fn current() {}'})

    def test_deprecation_alone_cannot_approve_a_removal_promise(self):
        line = 'The legacy API will be removed in a future release.'
        for assessment in ({}, {'supported':False, 'correction':'This is accurate.'}):
            result = self.decide(line, self.refs, **assessment)
            self.assertEqual((result['verdict'], len(result['findings'])), ('changes_requested', 1))
            self.assertIn('Remove the unsupported', result['findings'][0]['correction'])
        for claim, refs in [(line, {**self.refs,'docs/api.md:4':line}),
                            ('No policy specifies whether the legacy API will be removed.', self.refs),
                            ('The legacy API delegates to the current one.', self.refs)]:
            self.assertEqual(self.decide(claim, refs)['verdict'], 'approved')

    def test_cap_is_compared_in_the_same_units_against_the_pinned_retry_policy(self):
        refs = {**self.refs,'docs/api.md:4':'The retry delay has a ceiling of 30,000 milliseconds.'}
        for seconds, verdict in [(30,'approved'), (90,'changes_requested')]:
            result = self.decide(f'Both functions cap their output at {seconds} seconds.', refs)
            self.assertEqual(result['verdict'], verdict)
            if result['findings']:
                self.assertIn('docs/api.md:4', result['findings'][0]['correction'])

    def test_assessments_require_real_sources_and_complete_passage_coverage(self):
        line = 'The delay starts at 500 ms.'
        result = self.decide(line, self.refs, supported=False, correction='Use the implemented initial delay.')
        self.assertIn(line, result['findings'][0]['quote'])
        for invalid in ({'source':'src/lib.rs:999'}, {'passage':'C2'}, {'supported':False, 'correction':''}):
            with self.assertRaises(ValueError):
                self.decide(line, self.refs, **invalid)
        patch = self.diff(line) + '+//! Other fact.\\n'
        with self.assertRaises(ValueError):
            assess_passages({'assessments':[]}, prose_passages(patch), self.refs, patch)

    async def test_invalid_decisions_never_issue_an_approval(self):
        original = {'src/lib.rs':self.refs['src/lib.rs:1'].encode()}
        invalid = SimpleNamespace(type='function_call', name='record_documentation_review', arguments='{}')
        response = SimpleNamespace(status='completed', output=[invalid])
        create = AsyncMock(return_value=response)
        client = SimpleNamespace(responses=SimpleNamespace(create=create))
        with self.assertRaises(RepositoryError):
            await review_patch(client, original, original, self.diff('A claim.').encode())
        self.assertEqual(create.await_count, 2)
        invalid.arguments = json.dumps({'assessments':[{'passage':'C1','source':'src/lib.rs:1',
                                        'source_fact':'The API is deprecated.','supported':True,'correction':''}]})
        result = await review_patch(client, original, original, self.diff('Use the current API.').encode())
        self.assertEqual((result['model'],result['verdict']), (MODEL, 'approved'))
        self.assertEqual(result['evidence'], [{'location':'src/lib.rs:1','text':self.refs['src/lib.rs:1']}])
