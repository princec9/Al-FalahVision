# Part of Odoo. See LICENSE file for full copyright and licensing details.
"""Unit tests for Shopify Product Metafield push/pull synchronization.

Tests cover the utility functions that build GraphQL payloads and reconcile
metafields between Odoo and Shopify. No live Shopify API calls are made;
all GraphQL responses are mocked.

Run with:
    odoo-bin --test-enable -d test_db -i ecommerce_shopify_upsync --stop-after-init
"""
from unittest.mock import patch
from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from odoo.addons.ecommerce_shopify_upsync import utils_upsync


@tagged('post_install', '-at_install')
class TestPushProductMetafields(TransactionCase):
    """Test `utils_upsync.push_product_metafields_to_shopify`."""

    def setUp(self):
        super().setUp()
        self.Account = self.env['ecommerce.account']
        self.Metafield = self.env['shopify.product.metafield']
        self.Template = self.env['product.template']

        # The ecommerce_shopify module installs a channel with code 'shopify'
        # (data/ecommerce_channel_data.xml, noupdate=1). Reuse it — creating
        # another would violate the UNIQUE(code) constraint.
        channel = self.env['ecommerce.channel'].search(
            [('code', '=', 'shopify')], limit=1,
        )
        if not channel:
            channel = self.env['ecommerce.channel'].create({
                'name': 'Shopify Test Channel',
                'code': 'shopify',
                'sequence': 40,
            })
        self.account = self.Account.create({
            'name': 'Test Shopify Account',
            'company_id': self.env.company.id,
            'ecommerce_channel_id': channel.id,
            'fulfilled_by': 'ecommerce',
            'shopify_store': 'test-store',
            'shopify_access_token': 'test-token-abc123',
        })

        self.template = self.Template.create({
            'name': 'Test Product Metafields',
        })

    def _make_line(self, namespace, key, value, shopify_type='single_line_text_field',
                   shopify_metafield_id=False):
        """Create a shopify.product.metafield record."""
        return self.Metafield.create({
            'product_tmpl_id': self.template.id,
            'namespace': namespace,
            'key': key,
            'type': shopify_type,
            'value': value,
            'shopify_metafield_id': shopify_metafield_id,
        })

    # ------------------------------------------------------------------ #
    # TEST 1: Existing metafield — UPDATE in place                       #
    # ------------------------------------------------------------------ #
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_product_metafields_from_shopify')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_existing_metafield_update(self, mock_api, mock_fetch):
        """TEST 1: custom.brand already exists on Shopify — update by ID."""
        mock_fetch.return_value = [
            {'id': '47007476351232', 'namespace': 'custom', 'key': 'brand',
             'value': 'Oakley', 'type': 'single_line_text_field'},
        ]
        mock_api.return_value = {
            'data': {
                'metafieldsSet': {
                    'metafields': [
                        {'id': 'gid://shopify/Metafield/47007476351232',
                         'namespace': 'custom', 'key': 'brand'},
                    ],
                    'userErrors': [],
                },
            },
        }

        line = self._make_line('custom', 'brand', 'Ray-Ban')
        result = utils_upsync.push_product_metafields_to_shopify(
            self.account, '999999', [line],
        )

        self.assertIn(('custom', 'brand'), result['success'])
        self.assertEqual(result['success'][('custom', 'brand')], '47007476351232')
        self.assertEqual(result['errors'], [])

    # ------------------------------------------------------------------ #
    # TEST 2: New metafield — CREATE                                      #
    # ------------------------------------------------------------------ #
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_product_metafields_from_shopify')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_new_metafield_create(self, mock_api, mock_fetch):
        """TEST 2: custom.frame doesn't exist — create it."""
        mock_fetch.return_value = []
        mock_api.return_value = {
            'data': {
                'metafieldsSet': {
                    'metafields': [
                        {'id': 'gid://shopify/Metafield/555555555',
                         'namespace': 'custom', 'key': 'frame'},
                    ],
                    'userErrors': [],
                },
            },
        }

        line = self._make_line('custom', 'frame', 'DDAD')
        result = utils_upsync.push_product_metafields_to_shopify(
            self.account, '999999', [line],
        )

        self.assertIn(('custom', 'frame'), result['success'])
        self.assertEqual(result['success'][('custom', 'frame')], '555555555')
        self.assertEqual(result['errors'], [])

    # ------------------------------------------------------------------ #
    # TEST 3: Namespace mismatch — BLOCKED                               #
    # ------------------------------------------------------------------ #
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_product_metafields_from_shopify')
    def test_namespace_mismatch_blocked(self, mock_fetch):
        """TEST 3: customdd.brand vs custom.brand — BLOCKED, no duplicate."""
        mock_fetch.return_value = [
            {'id': '47007476351232', 'namespace': 'custom', 'key': 'brand',
             'value': 'Oakley', 'type': 'single_line_text_field'},
        ]

        line = self._make_line('customdd', 'brand', 'Oakley')
        result = utils_upsync.push_product_metafields_to_shopify(
            self.account, '999999', [line],
        )

        # Must be BLOCKED — no success
        self.assertEqual(result['success'], {})
        self.assertTrue(len(result['errors']) > 0)
        error_msgs = [e[2] for e in result['errors']]
        self.assertTrue(any('NAMESPACE MISMATCH' in m for m in error_msgs))

    # ------------------------------------------------------------------ #
    # TEST 4: Same namespace + key — UPDATE                               #
    # ------------------------------------------------------------------ #
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_product_metafields_from_shopify')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_same_ns_key_updates(self, mock_api, mock_fetch):
        """TEST 4: custom.brand matches — update path."""
        mock_fetch.return_value = [
            {'id': '111111111', 'namespace': 'custom', 'key': 'brand',
             'value': 'Old', 'type': 'single_line_text_field'},
        ]
        mock_api.return_value = {
            'data': {
                'metafieldsSet': {
                    'metafields': [{'id': 'gid://shopify/Metafield/111111111',
                                    'namespace': 'custom', 'key': 'brand'}],
                    'userErrors': [],
                },
            },
        }

        line = self._make_line('custom', 'brand', 'New')
        result = utils_upsync.push_product_metafields_to_shopify(
            self.account, '999999', [line],
        )

        self.assertIn(('custom', 'brand'), result['success'])
        self.assertEqual(result['errors'], [])

    # ------------------------------------------------------------------ #
    # TEST 5: Different namespace same key — BLOCKED                      #
    # ------------------------------------------------------------------ #
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_product_metafields_from_shopify')
    def test_diff_ns_same_key_blocked(self, mock_fetch):
        """TEST 5: custom.brand exists, Odoo has another_ns.brand — BLOCKED."""
        mock_fetch.return_value = [
            {'id': '111111111', 'namespace': 'custom', 'key': 'brand',
             'value': 'Oakley', 'type': 'single_line_text_field'},
        ]

        line = self._make_line('another_namespace', 'brand', 'TestBrand')
        result = utils_upsync.push_product_metafields_to_shopify(
            self.account, '999999', [line],
        )

        # Blocked by key-collision check
        self.assertEqual(result['success'], {})
        self.assertTrue(len(result['errors']) > 0)

    # ------------------------------------------------------------------ #
    # TEST 6: userErrors — operation fails                                #
    # ------------------------------------------------------------------ #
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_product_metafields_from_shopify')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_user_errors_handled(self, mock_api, mock_fetch):
        """TEST 6: Shopify userErrors are surfaced as errors."""
        mock_fetch.return_value = []
        mock_api.return_value = {
            'data': {
                'metafieldsSet': {
                    'metafields': [],
                    'userErrors': [
                        {'field': ['metafields', '0'],
                         'message': 'Namespace is required',
                         'code': 'BLANK'},
                    ],
                },
            },
        }

        line = self._make_line('custom', 'brand', 'Test')
        result = utils_upsync.push_product_metafields_to_shopify(
            self.account, '999999', [line],
        )

        self.assertEqual(result['success'], {})
        self.assertTrue(len(result['errors']) > 0)

    # ------------------------------------------------------------------ #
    # TEST 7: GraphQL top-level errors                                    #
    # ------------------------------------------------------------------ #
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_product_metafields_from_shopify')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_graphql_errors(self, mock_api, mock_fetch):
        """TEST 7: GraphQL-level errors raise ECommerceApiError."""
        mock_fetch.return_value = []
        mock_api.return_value = {
            'errors': [{'message': 'Access denied'}],
        }

        line = self._make_line('custom', 'brand', 'Test')
        with self.assertRaises(Exception):
            utils_upsync.push_product_metafields_to_shopify(
                self.account, '999999', [line],
            )

    # ------------------------------------------------------------------ #
    # TEST 9: Duplicate prevention                                        #
    # ------------------------------------------------------------------ #
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_product_metafields_from_shopify')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_duplicate_prevention(self, mock_api, mock_fetch):
        """TEST 9: Second push UPDATEs, doesn't CREATE again."""
        # First push: no existing metafields
        mock_fetch.return_value = []
        mock_api.return_value = {
            'data': {
                'metafieldsSet': {
                    'metafields': [{'id': 'gid://shopify/Metafield/333333333',
                                    'namespace': 'custom', 'key': 'brand'}],
                    'userErrors': [],
                },
            },
        }

        line = self._make_line('custom', 'brand', 'A')
        result1 = utils_upsync.push_product_metafields_to_shopify(
            self.account, '999999', [line],
        )
        self.assertIn(('custom', 'brand'), result1['success'])

        # Second push: Shopify now has the metafield. The Odoo row is the SAME
        # (product, namespace, key) record (shopify.product.metafield has a
        # UNIQUE(product, namespace, key) constraint) — its value is updated.
        line.write({'value': 'B'})
        mock_fetch.return_value = [
            {'id': '333333333', 'namespace': 'custom', 'key': 'brand',
             'value': 'B', 'type': 'single_line_text_field'},
        ]
        mock_api.return_value = {
            'data': {
                'metafieldsSet': {
                    'metafields': [{'id': 'gid://shopify/Metafield/333333333',
                                    'namespace': 'custom', 'key': 'brand'}],
                    'userErrors': [],
                },
            },
        }

        result2 = utils_upsync.push_product_metafields_to_shopify(
            self.account, '999999', [line],
        )
        self.assertIn(('custom', 'brand'), result2['success'])
        self.assertEqual(result2['success'][('custom', 'brand')], '333333333')
        self.assertEqual(result2['errors'], [])

    # ------------------------------------------------------------------ #
    # TEST: blank-value lines are skipped, not pushed                     #
    # ------------------------------------------------------------------ #
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_product_metafields_from_shopify')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_push_skips_blank_value(self, mock_api, mock_fetch):
        """Regression: pre-filled-but-unedited rows (e.g. the Google Shopping
        placeholders, created with value='') must never be sent to
        metafieldsSet — Shopify rejects a blank value outright ("Value can't
        be blank"), and this must not be reported as a push failure either."""
        mock_fetch.return_value = []
        line = self._make_line('mm-google-shopping', 'age_group', '')

        result = utils_upsync.push_product_metafields_to_shopify(
            self.account, '999999', [line],
        )

        self.assertEqual(result['success'], {})
        self.assertEqual(result['errors'], [])
        mock_api.assert_not_called()

    # ------------------------------------------------------------------ #
    # TEST: single_line_text_field newline is stripped before push        #
    # ------------------------------------------------------------------ #
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_product_metafields_from_shopify')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_push_single_line_text_strips_newline(self, mock_api, mock_fetch):
        """Regression: `global.description_tag` (SEO meta description) comes
        back from Shopify's own PULL with a trailing newline even though its
        type is single_line_text_field. Pushing it back unmodified is
        rejected by Shopify ("Value must be a single line text string.") —
        the newline must be stripped before it's sent."""
        mock_fetch.return_value = []
        mock_api.return_value = {
            'data': {
                'metafieldsSet': {
                    'metafields': [{'id': 'gid://shopify/Metafield/444444444',
                                    'namespace': 'global', 'key': 'description_tag'}],
                    'userErrors': [],
                },
            },
        }
        line = self._make_line('global', 'description_tag', 'Meta description\n')

        result = utils_upsync.push_product_metafields_to_shopify(
            self.account, '999999', [line],
        )

        self.assertIn(('global', 'description_tag'), result['success'])
        sent_mutation = mock_api.call_args.args[2]
        # _gql_string() escapes a real newline in the value as a literal
        # backslash-n; that escaped form must not appear for this field's
        # value (the mutation's own multi-line formatting has real newlines,
        # so a raw '\n' check would be a false positive).
        self.assertNotIn('value: "Meta description\\n"', sent_mutation)
        self.assertIn('value: "Meta description"', sent_mutation)

    # ------------------------------------------------------------------ #
    # TEST: same key, different namespace, same batch — no false mismatch #
    # ------------------------------------------------------------------ #
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_product_metafields_from_shopify')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_push_same_key_different_namespace_same_batch(self, mock_api, mock_fetch):
        """Regression: `custom.brand` and `customdd.brand` (same key, distinct
        namespaces, both legitimately already existing on Shopify) pushed in
        the same batch must both succeed. The response-validation used to
        match Shopify's returned metafields back to the requested line by key
        alone, so it always compared the `customdd.brand` response against
        whichever same-key line came first (`custom.brand`) and reported a
        bogus "namespace mismatch" even though Shopify's response was fine."""
        mock_fetch.return_value = [
            {'id': '47007476351232', 'namespace': 'custom', 'key': 'brand',
             'value': 'Oakley', 'type': 'single_line_text_field'},
            {'id': '47011194503424', 'namespace': 'customdd', 'key': 'brand',
             'value': 'Oakley', 'type': 'single_line_text_field'},
        ]
        mock_api.return_value = {
            'data': {
                'metafieldsSet': {
                    'metafields': [
                        {'id': 'gid://shopify/Metafield/47007476351232',
                         'namespace': 'custom', 'key': 'brand'},
                        {'id': 'gid://shopify/Metafield/47011194503424',
                         'namespace': 'customdd', 'key': 'brand'},
                    ],
                    'userErrors': [],
                },
            },
        }
        line1 = self._make_line('custom', 'brand', 'Oakley')
        line2 = self._make_line('customdd', 'brand', 'Oakley')

        result = utils_upsync.push_product_metafields_to_shopify(
            self.account, '999999', [line1, line2],
        )

        self.assertEqual(result['errors'], [])
        self.assertEqual(result['success'][('custom', 'brand')], '47007476351232')
        self.assertEqual(result['success'][('customdd', 'brand')], '47011194503424')

    # ------------------------------------------------------------------ #
    # TEST: product push never calls metafieldDefinitionCreate           #
    # ------------------------------------------------------------------ #
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_product_metafields_from_shopify')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_push_never_calls_metafield_definition_create(self, mock_api, mock_fetch):
        """Product push must only call metafieldsSet to update product values.
        It must never call metafieldDefinitionCreate, keeping definition sync
        and product value sync strictly decoupled."""
        mock_fetch.return_value = []
        mock_api.return_value = {
            'data': {
                'metafieldsSet': {
                    'metafields': [{'id': 'gid://shopify/Metafield/333333333',
                                    'namespace': 'custom', 'key': 'brand'}],
                    'userErrors': [],
                },
            },
        }
        line = self._make_line('custom', 'brand', 'Oakley')

        result = utils_upsync.push_product_metafields_to_shopify(
            self.account, '999999', [line],
        )

        self.assertEqual(result['errors'], [])
        self.assertIn(('custom', 'brand'), result['success'])
        definition_create_calls = [
            c for c in mock_api.call_args_list if 'metafieldDefinitionCreate' in c.args[2]
        ]
        self.assertEqual(len(definition_create_calls), 0)

    # ------------------------------------------------------------------ #
    # TEST: an existing definition is never re-created                    #
    # ------------------------------------------------------------------ #
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_product_metafields_from_shopify')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_push_skips_definition_create_when_already_exists(self, mock_api, mock_fetch):
        """A (namespace, key) that already has a Metafield Definition must
        never trigger a metafieldDefinitionCreate call on every push."""
        mock_fetch.return_value = []

        def side_effect(headers, url, query):
            if 'metafieldDefinitions(' in query:
                return {'data': {'metafieldDefinitions': {
                    'nodes': [{'id': 'gid://shopify/MetafieldDefinition/999', 'name': 'Brand',
                               'namespace': 'custom', 'key': 'brand', 'description': None,
                               'ownerType': 'PRODUCT', 'type': {'name': 'single_line_text_field'}}],
                    'pageInfo': {'hasNextPage': False, 'endCursor': None},
                }}}
            return {
                'data': {
                    'metafieldsSet': {
                        'metafields': [{'id': 'gid://shopify/Metafield/333333333',
                                        'namespace': 'custom', 'key': 'brand'}],
                        'userErrors': [],
                    },
                },
            }
        mock_api.side_effect = side_effect
        line = self._make_line('custom', 'brand', 'Oakley')

        result = utils_upsync.push_product_metafields_to_shopify(
            self.account, '999999', [line],
        )

        self.assertEqual(result['errors'], [])
        definition_create_calls = [
            c for c in mock_api.call_args_list if 'metafieldDefinitionCreate' in c.args[2]
        ]
        self.assertEqual(definition_create_calls, [])

    # ------------------------------------------------------------------ #
    # TEST: reserved/app-managed namespaces never get an auto-created     #
    # definition (global is Shopify-reserved; mm-google-shopping is       #
    # managed by Shopify's own Google & YouTube channel app)               #
    # ------------------------------------------------------------------ #
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_product_metafields_from_shopify')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_push_never_creates_definition_for_reserved_namespace(self, mock_api, mock_fetch):
        mock_fetch.return_value = []

        def side_effect(headers, url, query):
            if 'metafieldDefinitions(' in query:
                return {'data': {'metafieldDefinitions': {'nodes': [], 'pageInfo': {'hasNextPage': False, 'endCursor': None}}}}
            return {
                'data': {
                    'metafieldsSet': {
                        'metafields': [{'id': 'gid://shopify/Metafield/444444444',
                                        'namespace': 'global', 'key': 'description_tag'}],
                        'userErrors': [],
                    },
                },
            }
        mock_api.side_effect = side_effect
        line = self._make_line('global', 'description_tag', 'Some description')

        result = utils_upsync.push_product_metafields_to_shopify(
            self.account, '999999', [line],
        )

        self.assertEqual(result['errors'], [])
        definition_create_calls = [
            c for c in mock_api.call_args_list if 'metafieldDefinitionCreate' in c.args[2]
        ]
        self.assertEqual(definition_create_calls, [])

    # ------------------------------------------------------------------ #
    # TEST: a stale error on a now-blank row is cleared, not repeated     #
    # ------------------------------------------------------------------ #
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_product_metafields_from_shopify')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_push_clears_stale_error_on_blank_row(self, mock_api, mock_fetch):
        """Regression: a row that failed to push in a past attempt (before it
        was blanked, or before blank rows were skipped) must not keep showing
        a stale "Error" badge forever once it's blank and correctly skipped."""
        mock_fetch.return_value = []
        line = self._make_line('mm-google-shopping', 'age_group', '')
        line.write({
            'last_sync_status': 'error',
            'last_sync_error': "Value can't be blank.",
        })

        self.account._push_product_metafields_to_shopify('999999', self.template)

        self.assertFalse(line.last_sync_status)
        self.assertFalse(line.last_sync_error)
        mock_api.assert_not_called()

    # ------------------------------------------------------------------ #
    # TEST: customdd namespace is never created                           #
    # ------------------------------------------------------------------ #
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_product_metafields_from_shopify')
    def test_customdd_never_created(self, mock_fetch):
        """Regression: Odoo has customdd.brand, Shopify has custom.brand.
        The connector must NOT create customdd.brand."""
        mock_fetch.return_value = [
            {'id': '47007476351232', 'namespace': 'custom', 'key': 'brand',
             'value': 'Oakley', 'type': 'single_line_text_field'},
        ]

        line = self._make_line('customdd', 'brand', 'Oakley')
        result = utils_upsync.push_product_metafields_to_shopify(
            self.account, '999999', [line],
        )

        # Must NOT be in success — blocked
        self.assertEqual(result['success'], {})
        self.assertTrue(len(result['errors']) > 0)
        # The error message must contain "NAMESPACE MISMATCH"
        error_msgs = [e[2] for e in result['errors']]
        self.assertTrue(any('NAMESPACE MISMATCH' in m for m in error_msgs))
        # Error must mention blocking
        self.assertTrue(any('BLOCKED' in m for m in error_msgs))


@tagged('post_install', '-at_install')
class TestPullProductMetafields(TransactionCase):
    """Test `_pull_product_metafields_to_odoo`."""

    def setUp(self):
        super().setUp()
        self.Account = self.env['ecommerce.account']
        self.Metafield = self.env['shopify.product.metafield']
        self.Template = self.env['product.template']

        channel = self.env['ecommerce.channel'].search(
            [('code', '=', 'shopify')], limit=1,
        )
        if not channel:
            channel = self.env['ecommerce.channel'].create({
                'name': 'Shopify Test Channel',
                'code': 'shopify',
                'sequence': 40,
            })
        self.account = self.Account.create({
            'name': 'Test Shopify Account',
            'company_id': self.env.company.id,
            'ecommerce_channel_id': channel.id,
            'fulfilled_by': 'ecommerce',
            'shopify_store': 'test-store',
            'shopify_access_token': 'test-token-abc123',
        })
        self.template = self.Template.create({
            'name': 'Test Pull Product',
        })

    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_product_metafields_from_shopify')
    def test_pull_creates_new_metafields(self, mock_fetch):
        """PULL: Shopify has metafields, Odoo is empty — create them."""
        mock_fetch.return_value = [
            {'id': '47007476351232', 'namespace': 'custom', 'key': 'brand',
             'value': 'Oakley', 'type': 'single_line_text_field'},
            {'id': '4700747636000', 'namespace': 'custom', 'key': 'frame',
             'value': 'DDAD', 'type': 'single_line_text_field'},
        ]

        self.account._pull_product_metafields_to_odoo('999999', self.template)

        metafields = self.Metafield.search([
            ('product_tmpl_id', '=', self.template.id),
        ])
        self.assertEqual(len(metafields), 2)

        brand_mf = metafields.filtered(lambda m: m.key == 'brand')
        self.assertEqual(len(brand_mf), 1)
        self.assertEqual(brand_mf.namespace, 'custom')
        self.assertEqual(brand_mf.value, 'Oakley')
        self.assertEqual(brand_mf.shopify_metafield_id, 'gid://shopify/Metafield/47007476351232')
        self.assertEqual(brand_mf.last_sync_status, 'success')
        self.assertEqual(brand_mf.last_sync_direction, 'SHOPIFY_TO_ODOO')

        frame_mf = metafields.filtered(lambda m: m.key == 'frame')
        self.assertEqual(len(frame_mf), 1)
        self.assertEqual(frame_mf.namespace, 'custom')
        self.assertEqual(frame_mf.value, 'DDAD')
        self.assertEqual(frame_mf.shopify_metafield_id, 'gid://shopify/Metafield/4700747636000')
        self.assertEqual(frame_mf.last_sync_status, 'success')
        self.assertEqual(frame_mf.last_sync_direction, 'SHOPIFY_TO_ODOO')

    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_product_metafields_from_shopify')
    def test_pull_updates_existing_metafields(self, mock_fetch):
        """PULL: Odoo already has the metafield — update value, don't duplicate."""
        self.Metafield.create({
            'product_tmpl_id': self.template.id,
            'namespace': 'custom',
            'key': 'brand',
            'type': 'single_line_text_field',
            'value': 'OldValue',
            'shopify_metafield_id': '47007476351232',
        })

        mock_fetch.return_value = [
            {'id': '47007476351232', 'namespace': 'custom', 'key': 'brand',
             'value': 'NewValue', 'type': 'single_line_text_field'},
        ]

        self.account._pull_product_metafields_to_odoo('999999', self.template)

        metafields = self.Metafield.search([
            ('product_tmpl_id', '=', self.template.id),
        ])
        self.assertEqual(len(metafields), 1)
        self.assertEqual(metafields.value, 'NewValue')

    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_product_metafields_from_shopify')
    def test_pull_archives_deleted_metafields(self, mock_fetch):
        """PULL: Shopify removed a metafield — Odoo row is archived."""
        self.Metafield.create({
            'product_tmpl_id': self.template.id,
            'namespace': 'custom',
            'key': 'brand',
            'type': 'single_line_text_field',
            'value': 'Oakley',
            'shopify_metafield_id': '47007476351232',
        })

        mock_fetch.return_value = []

        self.account._pull_product_metafields_to_odoo('999999', self.template)

        active_mfs = self.Metafield.with_context(active_test=False).search([
            ('product_tmpl_id', '=', self.template.id),
        ])
        self.assertEqual(len(active_mfs), 1)
        self.assertFalse(active_mfs.active)

    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_product_metafields_from_shopify')
    def test_pull_legacy_string_type_normalized(self, mock_fetch):
        """PULL: a metafield reported with the legacy 'string' type (pre-dating
        Shopify's metafield definitions/type system) must not crash the pull —
        `type` is a closed Selection field and 'string' isn't one of its values.
        It should be normalized to 'single_line_text_field' instead."""
        mock_fetch.return_value = [
            {'id': '999000111', 'namespace': 'custom', 'key': 'legacy_field',
             'value': 'SomeValue', 'type': 'string'},
        ]

        self.account._pull_product_metafields_to_odoo('999999', self.template)

        metafields = self.Metafield.search([
            ('product_tmpl_id', '=', self.template.id),
        ])
        self.assertEqual(len(metafields), 1)
        self.assertEqual(metafields.type, 'single_line_text_field')
        self.assertEqual(metafields.value, 'SomeValue')

    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_product_metafields_from_shopify')
    def test_pull_unknown_type_falls_back_instead_of_raising(self, mock_fetch):
        """PULL: a genuinely unrecognized type (not a known legacy alias either)
        must fall back to 'single_line_text_field' rather than raising and
        failing the whole product's metafield pull."""
        mock_fetch.return_value = [
            {'id': '999000222', 'namespace': 'custom', 'key': 'weird_field',
             'value': 'X', 'type': 'totally_unknown_type'},
        ]

        self.account._pull_product_metafields_to_odoo('999999', self.template)

        metafields = self.Metafield.search([
            ('product_tmpl_id', '=', self.template.id),
        ])
        self.assertEqual(len(metafields), 1)
        self.assertEqual(metafields.type, 'single_line_text_field')


@tagged('post_install', '-at_install')
class TestBuildSeoInput(TransactionCase):
    """`_build_seo_input` feeds productCreate/productUpdate's `seo: {...}`
    block. Shopify stores seo.title/seo.description as the title_tag/
    description_tag metafields under the hood — both single_line_text_field
    — so the same newline constraint the generic metafields push already
    handles (see TestPullProductMetafields) applies here too."""

    def test_seo_description_newline_stripped(self):
        """Regression: `shopify_seo_description` is an Odoo Text field, so a
        user can type/paste a value with a newline. Sending that straight
        through fails the *entire* productUpdate/productCreate call with
        "Value must be a single line text string." — not just a metafield —
        since Shopify maps seo.description to the description_tag metafield."""
        seo = utils_upsync._build_seo_input({
            'seo_title': 'Great Product',
            'seo_description': 'Meta description\nwith a line break',
        })
        self.assertIn('title: "Great Product"', seo)
        self.assertIn('description: "Meta description with a line break"', seo)
        self.assertNotIn('\\n', seo)

    def test_seo_input_empty_when_both_blank(self):
        self.assertEqual(utils_upsync._build_seo_input({}), '')


@tagged('post_install', '-at_install')
class TestCreateMetafieldDefinitionOnShopify(TransactionCase):
    """Test `utils_upsync.create_metafield_definition_on_shopify`."""

    def setUp(self):
        super().setUp()
        channel = self.env['ecommerce.channel'].search([('code', '=', 'shopify')], limit=1)
        if not channel:
            channel = self.env['ecommerce.channel'].create({
                'name': 'Shopify Test Channel',
                'code': 'shopify',
                'sequence': 40,
            })
        self.account = self.env['ecommerce.account'].create({
            'name': 'Test Shopify Account',
            'company_id': self.env.company.id,
            'ecommerce_channel_id': channel.id,
            'fulfilled_by': 'ecommerce',
            'shopify_store': 'test-store',
            'shopify_access_token': 'test-token-abc123',
        })

    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_metafield_definitions_from_shopify')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_create_metafield_definition_creates_when_missing(self, mock_api, mock_fetch):
        """When definition does not exist on Shopify, call metafieldDefinitionCreate."""
        mock_fetch.return_value = []
        mock_api.return_value = {
            'data': {
                'metafieldDefinitionCreate': {
                    'createdDefinition': {
                        'id': 'gid://shopify/MetafieldDefinition/12345',
                        'name': 'Care Instructions',
                        'namespace': 'custom',
                        'key': 'care',
                    },
                    'userErrors': [],
                },
            },
        }

        res = utils_upsync.create_metafield_definition_on_shopify(
            self.account, 'custom', 'care', 'single_line_text_field', name='Care Instructions'
        )

        self.assertTrue(res['created'])
        self.assertFalse(res['reused'])
        self.assertEqual(res['definition_id'], '12345')
        self.assertIsNone(res['error'])
        self.assertEqual(mock_api.call_count, 1)
        self.assertIn('metafieldDefinitionCreate', mock_api.call_args[0][2])

    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_metafield_definitions_from_shopify')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_create_metafield_definition_reuses_when_already_exists(self, mock_api, mock_fetch):
        """When definition already exists on Shopify, reuse it and do not call metafieldDefinitionCreate."""
        mock_fetch.return_value = [
            {
                'shopify_definition_id': 'gid://shopify/MetafieldDefinition/999',
                'name': 'Care Instructions',
                'namespace': 'custom',
                'key': 'care',
                'owner_type': 'PRODUCT',
                'type': 'single_line_text_field',
            }
        ]

        res = utils_upsync.create_metafield_definition_on_shopify(
            self.account, 'custom', 'care', 'single_line_text_field', name='Care Instructions'
        )

        self.assertFalse(res['created'])
        self.assertTrue(res['reused'])
        self.assertEqual(res['definition_id'], '999')
        self.assertIsNone(res['error'])
        self.assertEqual(mock_api.call_count, 0)

    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.match_metafield_definition')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_metafield_definitions_from_shopify')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_create_metafield_definition_handles_taken_error(self, mock_api, mock_fetch, mock_match):
        """When Shopify returns TAKEN userError, treat as existing/successful (reused)."""
        mock_fetch.return_value = []
        mock_api.return_value = {
            'data': {
                'metafieldDefinitionCreate': {
                    'createdDefinition': None,
                    'userErrors': [
                        {
                            'code': 'TAKEN',
                            'field': ['definition', 'key'],
                            'message': 'Key has already been taken',
                        }
                    ],
                },
            },
        }
        mock_match.return_value = {
            'shopify_definition_id': 'gid://shopify/MetafieldDefinition/777',
            'name': 'Care',
            'namespace': 'custom',
            'key': 'care',
        }

        res = utils_upsync.create_metafield_definition_on_shopify(
            self.account, 'custom', 'care', 'single_line_text_field', name='Care'
        )

        self.assertFalse(res['created'])
        self.assertTrue(res['reused'])
        self.assertEqual(res['definition_id'], '777')
        self.assertIsNone(res['error'])


@tagged('post_install', '-at_install')
class TestShopifyMetafieldSyncTrackingFields(TransactionCase):
    """Unit tests verifying Shopify Metafield Sync Tracking Fields in Odoo:
    - shopify_metafield_id stored in canonical GID format ('gid://shopify/Metafield/...')
    - last_sync_status ('success', 'failed')
    - last_sync_direction ('SHOPIFY_TO_ODOO' for Pull, 'ODOO_TO_SHOPIFY' for Push)
    - Sync Metafields sets direction based on actual operation performed
    - Metafield ID vs Definition ID separation
    - Action buttons return client reload action
    """

    def setUp(self):
        super().setUp()
        self.Account = self.env['ecommerce.account']
        self.Metafield = self.env['shopify.product.metafield']
        self.Definition = self.env['shopify.metafield.definition']
        self.Template = self.env['product.template']
        self.Offer = self.env['ecommerce.offer']

        channel = self.env['ecommerce.channel'].search([('code', '=', 'shopify')], limit=1)
        if not channel:
            channel = self.env['ecommerce.channel'].create({
                'name': 'Shopify Test Channel',
                'code': 'shopify',
                'sequence': 40,
            })
        self.channel = channel
        self.account = self.Account.create({
            'name': 'Test Shopify Account',
            'company_id': self.env.company.id,
            'ecommerce_channel_id': channel.id,
            'fulfilled_by': 'ecommerce',
            'shopify_store': 'test-store',
            'shopify_access_token': 'test-token-abc123',
            'state': 'connected',
        })
        self.template = self.Template.create({
            'name': 'Tracking Test Product',
        })
        self.offer = self.Offer.create({
            'sku': 'SKU-TRACKING-TEST',
            'name': 'Tracking Test Product Offer',
            'ecommerce_account_id': self.account.id,
            'matched_product_id': self.template.product_variant_id.id,
            'ec_product_template_identifier': '999111',
            'matched_product_template_id': self.template.id,
        })

    def test_new_metafield_default_status_is_empty(self):
        """A freshly created metafield in Odoo has empty last_sync_status and direction."""
        mf = self.Metafield.create({
            'product_tmpl_id': self.template.id,
            'namespace': 'custom',
            'key': 'material',
            'type': 'single_line_text_field',
            'value': 'Cotton',
        })
        self.assertFalse(mf.last_sync_status, "Un-synced metafield must not have a default 'success' status")
        self.assertFalse(mf.last_sync_direction)
        self.assertFalse(mf.shopify_metafield_id)

    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_product_metafields_from_shopify')
    def test_pull_metafield_tracking_fields(self, mock_fetch):
        """Pulling from Shopify stores canonical GID, status=success, direction=SHOPIFY_TO_ODOO."""
        mock_fetch.return_value = [
            {
                'id': 'gid://shopify/Metafield/888001',
                'gid': 'gid://shopify/Metafield/888001',
                'namespace': 'custom',
                'key': 'care_label',
                'type': 'single_line_text_field',
                'value': 'Hand wash cold',
            }
        ]

        action = self.template.action_pull_shopify_metafields()

        # Action must return reload
        self.assertEqual(action.get('type'), 'ir.actions.client')
        self.assertEqual(action.get('params', {}).get('next'), {'type': 'ir.actions.client', 'tag': 'reload'})

        mf = self.Metafield.search([
            ('product_tmpl_id', '=', self.template.id),
            ('namespace', '=', 'custom'),
            ('key', '=', 'care_label'),
        ])
        self.assertEqual(len(mf), 1)
        self.assertEqual(mf.shopify_metafield_id, 'gid://shopify/Metafield/888001')
        self.assertEqual(mf.last_sync_status, 'success')
        self.assertEqual(mf.last_sync_direction, 'SHOPIFY_TO_ODOO')
        self.assertTrue(mf.last_sync_date)
        self.assertFalse(mf.last_sync_error)

    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_product_metafields_from_shopify')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_push_create_metafield_tracking_fields(self, mock_api, mock_fetch):
        """Push creating a metafield sets returned GID, status=success, direction=ODOO_TO_SHOPIFY."""
        mock_fetch.return_value = []
        mock_api.return_value = {
            'data': {
                'metafieldsSet': {
                    'metafields': [
                        {
                            'id': 'gid://shopify/Metafield/888002',
                            'namespace': 'custom',
                            'key': 'origin',
                        }
                    ],
                    'userErrors': [],
                }
            }
        }

        mf = self.Metafield.create({
            'product_tmpl_id': self.template.id,
            'namespace': 'custom',
            'key': 'origin',
            'type': 'single_line_text_field',
            'value': 'Italy',
        })

        action = self.template.action_push_shopify_metafields()
        self.assertEqual(action.get('type'), 'ir.actions.client')
        self.assertEqual(action.get('params', {}).get('next'), {'type': 'ir.actions.client', 'tag': 'reload'})

        mf.invalidate_recordset()
        self.assertEqual(mf.shopify_metafield_id, 'gid://shopify/Metafield/888002')
        self.assertEqual(mf.last_sync_status, 'success')
        self.assertEqual(mf.last_sync_direction, 'ODOO_TO_SHOPIFY')
        self.assertTrue(mf.last_sync_date)
        self.assertFalse(mf.last_sync_error)

    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_product_metafields_from_shopify')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_push_update_metafield_tracking_fields(self, mock_api, mock_fetch):
        """Push updating keeps existing shopify_metafield_id, status=success, direction=ODOO_TO_SHOPIFY."""
        existing_gid = 'gid://shopify/Metafield/888003'
        mock_fetch.return_value = [
            {
                'id': existing_gid,
                'gid': existing_gid,
                'namespace': 'custom',
                'key': 'warranty',
                'type': 'single_line_text_field',
                'value': '1 Year',
            }
        ]
        mock_api.return_value = {
            'data': {
                'metafieldsSet': {
                    'metafields': [
                        {
                            'id': existing_gid,
                            'namespace': 'custom',
                            'key': 'warranty',
                        }
                    ],
                    'userErrors': [],
                }
            }
        }

        mf = self.Metafield.create({
            'product_tmpl_id': self.template.id,
            'namespace': 'custom',
            'key': 'warranty',
            'type': 'single_line_text_field',
            'value': '2 Years',
            'shopify_metafield_id': existing_gid,
        })

        action = self.template.action_push_shopify_metafields()
        self.assertEqual(action.get('type'), 'ir.actions.client')
        self.assertEqual(action.get('params', {}).get('next'), {'type': 'ir.actions.client', 'tag': 'reload'})

        mf.invalidate_recordset()
        self.assertEqual(mf.shopify_metafield_id, existing_gid)
        self.assertEqual(mf.last_sync_status, 'success')
        self.assertEqual(mf.last_sync_direction, 'ODOO_TO_SHOPIFY')
        self.assertFalse(mf.last_sync_error)

    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_product_metafields_from_shopify')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_push_failed_metafield_tracking_fields(self, mock_api, mock_fetch):
        """Failed push sets status=failed, direction=ODOO_TO_SHOPIFY, records error, preserves ID."""
        existing_gid = 'gid://shopify/Metafield/888004'
        mock_fetch.return_value = []
        mock_api.return_value = {
            'data': {
                'metafieldsSet': {
                    'metafields': [],
                    'userErrors': [
                        {
                            'field': ['metafields', '0'],
                            'message': 'Value exceeds maximum length',
                            'code': 'TOO_LONG',
                        }
                    ],
                }
            }
        }

        mf = self.Metafield.create({
            'product_tmpl_id': self.template.id,
            'namespace': 'custom',
            'key': 'long_text',
            'type': 'single_line_text_field',
            'value': 'X' * 500,
            'shopify_metafield_id': existing_gid,
        })

        action = self.template.action_push_shopify_metafields()
        self.assertEqual(action.get('type'), 'ir.actions.client')
        self.assertEqual(action.get('params', {}).get('next'), {'type': 'ir.actions.client', 'tag': 'reload'})

        mf.invalidate_recordset()
        self.assertEqual(mf.shopify_metafield_id, existing_gid, "Existing ID must not be wiped on failure")
        self.assertEqual(mf.last_sync_status, 'failed')
        self.assertEqual(mf.last_sync_direction, 'ODOO_TO_SHOPIFY')
        self.assertTrue(mf.last_sync_error)
        self.assertIn('Value exceeds maximum length', mf.last_sync_error)

    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_product_metafields_from_shopify')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_sync_metafields_sets_direction_per_operation(self, mock_api, mock_fetch):
        """action_sync_shopify_metafields pulls first (SHOPIFY_TO_ODOO), then pushes local lines (ODOO_TO_SHOPIFY)."""
        pulled_gid = 'gid://shopify/Metafield/888005'
        pushed_gid = 'gid://shopify/Metafield/888006'

        # Shopify already has custom.pulled_field
        mock_fetch.return_value = [
            {
                'id': pulled_gid,
                'gid': pulled_gid,
                'namespace': 'custom',
                'key': 'pulled_field',
                'type': 'single_line_text_field',
                'value': 'Remote Value',
            }
        ]
        # Push response for custom.local_field
        mock_api.return_value = {
            'data': {
                'metafieldsSet': {
                    'metafields': [
                        {
                            'id': pushed_gid,
                            'namespace': 'custom',
                            'key': 'local_field',
                        }
                    ],
                    'userErrors': [],
                }
            }
        }

        # Odoo has custom.local_field which is not on Shopify
        local_line = self.Metafield.create({
            'product_tmpl_id': self.template.id,
            'namespace': 'custom',
            'key': 'local_field',
            'type': 'single_line_text_field',
            'value': 'Local Only Value',
        })

        action = self.template.action_sync_shopify_metafields()
        self.assertEqual(action.get('type'), 'ir.actions.client')
        self.assertEqual(action.get('params', {}).get('next'), {'type': 'ir.actions.client', 'tag': 'reload'})

        pulled_line = self.Metafield.search([
            ('product_tmpl_id', '=', self.template.id),
            ('namespace', '=', 'custom'),
            ('key', '=', 'pulled_field'),
        ])
        self.assertTrue(pulled_line)
        self.assertEqual(pulled_line.shopify_metafield_id, pulled_gid)
        self.assertEqual(pulled_line.last_sync_status, 'success')
        self.assertEqual(pulled_line.last_sync_direction, 'SHOPIFY_TO_ODOO')

        local_line.invalidate_recordset()
        self.assertEqual(local_line.shopify_metafield_id, pushed_gid)
        self.assertEqual(local_line.last_sync_status, 'success')
        self.assertEqual(local_line.last_sync_direction, 'ODOO_TO_SHOPIFY')

    def test_metafield_id_never_overwritten_with_definition_id(self):
        """Shopify Metafield ID must never be overwritten with Metafield Definition ID."""
        defn = self.Definition.create({
            'ecommerce_account_id': self.account.id,
            'name': 'Test Def',
            'namespace': 'custom',
            'key': 'test_distinct_id',
            'type': 'single_line_text_field',
            'shopify_definition_id': 'gid://shopify/MetafieldDefinition/9999999',
        })

        # Finding the auto-populated product metafield row for this definition
        mf = self.Metafield.search([
            ('product_tmpl_id', '=', self.template.id),
            ('definition_id', '=', defn.id),
        ], limit=1)
        self.assertTrue(mf, "Definition should be automatically available on the product template")
        mf.write({
            'value': 'Value',
            'shopify_metafield_id': 'gid://shopify/Metafield/1111111',
        })

        self.assertNotEqual(mf.shopify_metafield_id, defn.shopify_definition_id)
        self.assertEqual(mf.shopify_metafield_id, 'gid://shopify/Metafield/1111111')
        self.assertEqual(defn.shopify_definition_id, '9999999')
        self.assertTrue(mf.shopify_metafield_id.startswith('gid://shopify/Metafield/'))


