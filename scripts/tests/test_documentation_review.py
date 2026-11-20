import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'integrations/moyai'))
from documentation_review import (MODEL, assess_passages, caller_note_finding, numeric_retry_claim, prose_passages,
                                  reference_lines, retry_formula_sources, retry_implementation, review_patch)
from repository_ledger import RepositoryError


class DocumentationReviewTests(unittest.IsolatedAsyncioTestCase):
    path = 'tests/compat.rs'
    refs = {'src/lib.rs:1': '#[deprecated(note = "use current")]'}
    capped_retry = {'src/lib.rs': b'pub fn bounded_backoff_ms(n: u32) -> u64 {\n'
                    b' 100_u64.saturating_mul(2_u64.saturating_pow(n)).min(30_000)\n}\n'
                    b'pub fn legacy_retry_delay_ms(n: u32) -> u64 { bounded_backoff_ms(n) }\n'}
    def decide(self, line, refs, **assessment):
        patch = self.diff(line)
        item = {'passage':'C1','source':'src/lib.rs:1','source_fact':'The API is deprecated.',
                'supported':True,'correction':'', **assessment}
        return assess_passages({'assessments':[item]}, prose_passages(patch), refs, patch)

    def diff(self, line):
        return f'--- /dev/null\n+++ b/{self.path}\n@@ -0,0 +1 @@\n+//! {line}\n'

    async def test_approved_formula_citation_cannot_override_false_concrete_examples(self):
        candidate = self.capped_retry
        formula = retry_implementation(candidate, reference_lines(candidate, candidate))
        patch = self.diff('Attempt 0 and 65536 both return 100 ms (low 16 bits: 0)').encode()
        patch += (b'+//! - Attempt 8 and 65544 both return 25,600 ms (low 16 bits: 8)\n'
                  b'+//! - Attempt 65536 and 65537 both return 30,000 ms (low 16 bits: 0, but high bits differ)\n'
                  b'+    assert_eq!(bounded_backoff_ms(65536), 30_000);\n')
        item = {'passage':'C1','source':formula,'source_fact':'The formula caps at 30,000 ms.',
                'supported':True,'correction':''}
        call = SimpleNamespace(type='function_call',name='record_documentation_review',
                               arguments=json.dumps({'assessments':[item]}))
        create = AsyncMock(return_value=SimpleNamespace(status='completed',output=[call]))
        client = SimpleNamespace(responses=SimpleNamespace(create=create))
        report = await review_patch(client, candidate, candidate, patch)
        self.assertEqual(report['verdict'],'changes_requested')
        self.assertIn('"65536": 30000', create.await_args.kwargs['input'])
        finding, = report['findings']
        for fact in ('65536 returns 30000 ms','65544 returns 30000 ms','bits of 65537 equal 1',
                     'attempts 65536 and 65537 match'):
            self.assertIn(fact, finding['correction'])
        for claim in ('Attempts 0 and 65_536 return 100 ms and 30 seconds respectively.',
                      'Attempts 8 and 65,544 return 25,600 ms and 30,000 ms respectively.',
                      'Attempts 4294967294 and 4294967295 both return 30 seconds.',
                      'If the input wrapped, attempts 0 and 65536 both return 100 ms.'):
            result = await review_patch(client, candidate, candidate, self.diff(claim).encode())
            self.assertEqual(result['verdict'],'approved',claim)

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
        files = self.capped_retry
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

    def test_inputs_cannot_be_documented_as_impossible_delays(self):
        loc = 'src/lib.rs:1'
        refs = {loc:'100_u64.saturating_mul(2_u64.saturating_pow(attempt)).min(30_000)'}
        args = {'assessments':[{'passage':'C1','source':loc,'source_fact':'Minimum 100 ms.',
                                'supported':True,'correction':''}]}
        for claim, expected in [('The small delays (0 and 2) are computed independently.','changes_requested'),
                                ('Attempts 0 and 2 have delays (100 and 400 ms).','approved')]:
            patch = self.diff(claim)
            decision = assess_passages(args, prose_passages(patch), refs, patch, loc)
            self.assertEqual(decision['verdict'], expected)

    async def test_byte_boundary_explanations_must_match_the_candidate_input_path(self):
        source = {'src/lib.rs': b'pub fn bounded_backoff_ms(index: u32) -> u64 {\n'
                  b' 100_u64.saturating_mul(2_u64.saturating_pow(index)).min(30_000)\n}\n'
                  b'pub fn legacy_retry_delay_ms(n: u32) -> u64 { bounded_backoff_ms(n) }\n'}
        claims = ['The attempt number is narrowed to a byte before calculating the delay, '
                  'which would make 256 look like an earlier attempt.',
                  'The calculation correctly handles the narrowing of the attempt number to a byte.',
                  'Narrowing to a byte would make 256 and 257 look like attempts 0 and 1.',
                  'If the attempt is narrowed to u8, 256 would wrap to 0.',
                  'The tests prevent a regression where the attempt number is narrowed to a byte.',
                  'A caller converts the attempt to a byte before calling the helper.',
                  'The attempt is not narrowed to a byte.',
                  'Both helpers return 30,000 ms for attempts 255, 256 and 257.']
        patch = (self.diff(claims[0]) + ''.join('+//! ' + line + '\n' for line in claims[1:])).encode()

        async def run(candidate, patch, citation):
            # Reproduce the old reviewer's blanket approval, even with a valid formula citation.
            items = [{'passage':key, 'source':citation, 'source_fact':'The calculation caps the delay.',
                      'supported':True, 'correction':''} for key in prose_passages(patch.decode())]
            call = SimpleNamespace(type='function_call', name='record_documentation_review',
                                   arguments=json.dumps({'assessments':items}))
            create = AsyncMock(return_value=SimpleNamespace(status='completed', output=[call]))
            decision = await review_patch(SimpleNamespace(responses=SimpleNamespace(create=create)),
                                          source, candidate, patch)
            return decision, create.await_args.kwargs['input']

        decision, prompt = await run(source, patch, 'src/lib.rs:2')
        self.assertEqual(decision['verdict'], 'changes_requested')
        self.assertEqual([f['quote'] for f in decision['findings']], ['//! ' + line for line in claims[:2]])
        for finding in decision['findings']:
            self.assertEqual(finding['basis'], 'implementation')
            self.assertIn('src/lib.rs:2', finding['correction'])
        self.assertIn('converted to a byte: C1, C2.', prompt)

        # A real conversion before the very same formula invalidates the direct-input proof.
        changed = {'src/lib.rs':source['src/lib.rs'].replace(b' 100_u64',
                   b' let index = u32::from(index as u8);\n 100_u64')}
        decision, prompt = await run(changed, self.diff('The attempt is narrowed to a byte.').encode(),
                                     'src/lib.rs:3')
        self.assertEqual(decision['verdict'], 'approved')
        self.assertNotIn('takes the original u32 attempt', prompt)

    def test_numeric_retry_passages_include_tables_and_inline_assertions(self):
        for claim in ('current(9) → 30,000 ms', 'Returns 400 milliseconds.', 'bounded_backoff_ms(2) = 400',
                      '| Attempt | Delay (ms) | 2 | 400 |',
                      'assert_eq!(legacy_retry_delay_ms(u32::MAX), 30_000); // capped',
                      'A 30-second ceiling applies to each retry delay.'):
            with self.subTest(claim=claim):
                self.assertTrue(numeric_retry_claim({'lines':[claim]}))
        for claim in ('Migration advice for the retry API in issue #38.',
                      'Two retry APIs: legacy → current.', 'The build took 400 ms.'):
            self.assertFalse(numeric_retry_claim({'lines':[claim]}))

    async def test_numeric_review_requires_the_model_to_cite_the_actual_calculation(self):
        source = {'src/lib.rs': b'// unrelated cap .min(5)\n'
                  b'pub fn bounded_backoff_ms(n: u32) -> u64 {\n'
                  b' // misleading calculation: .min(1)\n'
                  b' 100_u64.saturating_mul(2_u64.saturating_pow(n)).min(30_000)\n}\n'
                  b'#[deprecated(note = "use bounded_backoff_ms")]\n'
                  b'pub fn legacy_retry_delay_ms(n: u32) -> u64 { bounded_backoff_ms(n) }\n'}
        amount = 'Attempt 2 returns 400 ms.'
        patch = (self.diff(amount) + '+//! Migrate to bounded_backoff_ms.\n').encode()
        signature, formula, migration = ('src/lib.rs:2', 'src/lib.rs:4', 'src/lib.rs:6')
        self.assertEqual(retry_formula_sources(source, reference_lines(source, source)), (formula,))
        def response(citation):
            items = [{'passage':'C1','source':citation,'source_fact':'100 times 2 squared is 400.',
                      'supported':True,'correction':''},
                     {'passage':'C2','source':migration,'source_fact':'The attribute recommends bounded_backoff_ms.',
                      'supported':True,'correction':''}]
            call = SimpleNamespace(type='function_call', name='record_documentation_review',
                                   arguments=json.dumps({'assessments':items}))
            return SimpleNamespace(status='completed', output=[call])

        create = AsyncMock(side_effect=[response(migration), response(formula)])
        client = SimpleNamespace(responses=SimpleNamespace(create=create))
        result = await review_patch(client, source, source, patch)
        self.assertEqual(create.await_count, 2)
        self.assertIn(formula, create.await_args.kwargs['instructions'])
        self.assertEqual(result['verdict'], 'approved')
        self.assertEqual([p['source'] for p in result['passages'].values()], [formula, migration])
        create.side_effect = [response(signature), response(migration)]
        with self.assertRaisesRegex(RepositoryError, 'no valid decision'):
            await review_patch(client, source, source, patch)

        no_formula = {'src/lib.rs': source['src/lib.rs'].replace(
            b'100_u64.saturating_mul(2_u64.saturating_pow(n)).min(30_000)', b'other(n)')}
        create.reset_mock()
        with self.assertRaisesRegex(RepositoryError, 'without a source line'):
            await review_patch(client, no_formula, no_formula, patch)
        create.assert_not_awaited()

    def test_requested_caller_note_requires_module_docs_and_real_migration_evidence(self):
        loc = 'src/lib.rs:2'
        refs = {loc:'100_u64.saturating_mul(2_u64.saturating_pow(n)).min(30_000)',
                'src/lib.rs:4':'#[deprecated(note = "replace with the shared bounded_backoff_ms policy")]'}
        note = ('//! Each call is independent. bounded_backoff_ms computes the delay.\n'
                '//! legacy_retry_delay_ms forwards to it. Use bounded_backoff_ms.\n')
        for contents, missing in [('//! The results are independent.\n', True),
                                  (note.replace('//!', '//'), True), (note, False)]:
            finding = caller_note_finding({}, {self.path:contents.encode()}, refs, loc)
            self.assertEqual(bool(finding), missing)
            if finding:
                self.assertEqual(finding['sources'], [loc, 'src/lib.rs:4'])

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
