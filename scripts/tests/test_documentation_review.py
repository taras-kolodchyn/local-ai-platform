import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'integrations/moyai'))
from documentation_review import MODEL, assess_passages, prose_passages, reference_lines, retry_implementation, review_patch
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

    def test_only_the_legacy_wrapper_delegates_in_the_verified_implementation(self):
        files = {'src/lib.rs': b'pub fn bounded_backoff_ms(n: u32) -> u64 {\n'
                 b' 100_u64.saturating_mul(2_u64.saturating_pow(n)).min(30_000)\n}\n'
                 b'pub fn legacy_retry_delay_ms(n: u32) -> u64 {\n bounded_backoff_ms(n)\n}\n'}
        refs = reference_lines(files,files)
        loc = retry_implementation(files,refs)
        self.assertEqual(loc,'src/lib.rs:2')
        for claim, expected in [('Both functions delegate to the same capped implementation.','changes_requested'),
                                ('Both functions share the same capped behavior.','approved')]:
            patch = self.diff(claim)
            args = {'assessments':[{'passage':'C1','source':loc,'source_fact':'The current API caps the result.',
                                    'supported':True,'correction':''}]}
            decision = assess_passages(args,prose_passages(patch),refs,patch,loc)
            self.assertEqual(decision['verdict'],expected)

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
