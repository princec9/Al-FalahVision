# Part of Odoo. See LICENSE file for full copyright and licensing details.
"""Unit tests for Shopify Product Metafield Definitions in Odoo.

Tests cover:
- Centralized Metafield Definition configuration (Name, Namespace, Key, Type, Description, Shopify Definition ID)
- Automatic availability of definitions on all products
- Separate per-product values for the same definition
- Prevention of duplicate definitions and duplicate product values
- Pull / Sync from Shopify: fetching definitions and product values, mapping to correct products by Namespace + Key,
  maintaining Shopify IDs and sync status.
"""
from unittest.mock import patch

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestShopifyMetafieldDefinitions(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Account = self.env['ecommerce.account']
        self.Definition = self.env['shopify.metafield.definition']
        self.ProductMetafield = self.env['shopify.product.metafield']
        self.Template = self.env['product.template']
        self.Offer = self.env['ecommerce.offer']

        # Ensure Shopify channel
        channel = self.env['ecommerce.channel'].search([('code', '=', 'shopify')], limit=1)
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
            'state': 'connected',
        })

    def test_centralized_definition_creation_and_fields(self):
        """Verify centralized definition creation with all required and optional fields."""
        defn = self.Definition.create({
            'name': 'Care Instructions',
            'namespace': 'custom',
            'key': 'care_instructions',
            'type': 'single_line_text_field',
            'description': 'Fabric washing and care details',
            'shopify_definition_id': '1011121314',
            'ecommerce_account_id': self.account.id,
        })
        self.assertEqual(defn.name, 'Care Instructions')
        self.assertEqual(defn.namespace, 'custom')
        self.assertEqual(defn.key, 'care_instructions')
        self.assertEqual(defn.type, 'single_line_text_field')
        self.assertEqual(defn.type_name, 'single_line_text_field')
        self.assertEqual(defn.description, 'Fabric washing and care details')
        self.assertEqual(defn.shopify_definition_id, '1011121314')
        self.assertEqual(defn.ecommerce_account_id.id, self.account.id)

    def test_definition_auto_available_on_products_with_separate_values(self):
        """Verify each definition is automatically available on all products,
        and each product stores its own separate value for that definition."""
        prod1 = self.Template.create({'name': 'T-Shirt'})
        prod2 = self.Template.create({'name': 'Jeans'})

        # Create definition A: Care Instructions
        defn_care = self.Definition.create({
            'name': 'Care Instructions',
            'namespace': 'custom',
            'key': 'care_instructions',
            'type': 'single_line_text_field',
            'ecommerce_account_id': self.account.id,
        })

        # Both existing products should have a line for defn_care
        mf1 = self.ProductMetafield.search([
            ('product_tmpl_id', '=', prod1.id),
            ('definition_id', '=', defn_care.id),
        ])
        mf2 = self.ProductMetafield.search([
            ('product_tmpl_id', '=', prod2.id),
            ('definition_id', '=', defn_care.id),
        ])
        self.assertTrue(mf1, "Product 1 must automatically have a metafield for Care Instructions")
        self.assertTrue(mf2, "Product 2 must automatically have a metafield for Care Instructions")
        self.assertEqual(mf1.name, 'Care Instructions')
        self.assertEqual(mf2.name, 'Care Instructions')

        # Store separate values for each product
        mf1.write({'value': 'Machine wash cold'})
        mf2.write({'value': 'Dry clean only'})

        self.assertEqual(mf1.value, 'Machine wash cold')
        self.assertEqual(mf2.value, 'Dry clean only')
        # Verify modifying prod1's value does not alter prod2's value
        self.assertNotEqual(mf1.value, mf2.value)

        # Now create Definition B: Material
        defn_material = self.Definition.create({
            'name': 'Material',
            'namespace': 'custom',
            'key': 'material',
            'type': 'single_line_text_field',
            'ecommerce_account_id': self.account.id,
        })

        # Both products should now also have Material, while keeping their Care Instructions values
        mat1 = self.ProductMetafield.search([
            ('product_tmpl_id', '=', prod1.id),
            ('definition_id', '=', defn_material.id),
        ])
        mat2 = self.ProductMetafield.search([
            ('product_tmpl_id', '=', prod2.id),
            ('definition_id', '=', defn_material.id),
        ])
        self.assertTrue(mat1)
        self.assertTrue(mat2)
        mat1.write({'value': '100% Organic Cotton'})
        mat2.write({'value': '98% Cotton, 2% Elastane'})

        self.assertEqual(mf1.value, 'Machine wash cold')
        self.assertEqual(mf2.value, 'Dry clean only')
        self.assertEqual(mat1.value, '100% Organic Cotton')
        self.assertEqual(mat2.value, '98% Cotton, 2% Elastane')

        # Now create a NEW Product 3: should automatically receive both definitions
        prod3 = self.Template.create({'name': 'Hoodie'})
        prod3_mfs = self.ProductMetafield.search([
            ('product_tmpl_id', '=', prod3.id),
        ])
        self.assertIn(defn_care.id, prod3_mfs.mapped('definition_id.id'))
        self.assertIn(defn_material.id, prod3_mfs.mapped('definition_id.id'))

        prod3_care = prod3_mfs.filtered(lambda m: m.definition_id == defn_care)
        prod3_care.write({'value': 'Warm hand wash'})
        self.assertEqual(prod3_care.value, 'Warm hand wash')
        self.assertEqual(mf1.value, 'Machine wash cold')
        self.assertEqual(mf2.value, 'Dry clean only')

    def test_prevent_duplicate_definitions(self):
        """Verify duplicate definitions with the same (namespace, key, account) or ID are blocked."""
        self.Definition.create({
            'name': 'Warranty Period',
            'namespace': 'custom',
            'key': 'warranty_period',
            'type': 'single_line_text_field',
            'shopify_definition_id': 'def_unique_001',
            'ecommerce_account_id': self.account.id,
        })

        # Duplicate namespace + key on same account
        with self.assertRaises(Exception):
            self.Definition.create({
                'name': 'Warranty Period Duplicate',
                'namespace': 'custom',
                'key': 'warranty_period',
                'type': 'single_line_text_field',
                'ecommerce_account_id': self.account.id,
            })

        # Duplicate shopify_definition_id on same account
        with self.assertRaises(Exception):
            self.Definition.create({
                'name': 'Different Key',
                'namespace': 'custom',
                'key': 'different_key',
                'type': 'single_line_text_field',
                'shopify_definition_id': 'def_unique_001',
                'ecommerce_account_id': self.account.id,
            })

    def test_prevent_duplicate_product_metafield_values(self):
        """Verify that a product cannot have duplicate metafield rows for the same namespace + key."""
        prod = self.Template.create({'name': 'Test Bag'})
        self.ProductMetafield.create({
            'product_tmpl_id': prod.id,
            'namespace': 'custom',
            'key': 'strap_length',
            'type': 'single_line_text_field',
            'value': '120cm',
        })

        with self.assertRaises(Exception):
            self.ProductMetafield.create({
                'product_tmpl_id': prod.id,
                'namespace': 'custom',
                'key': 'strap_length',
                'type': 'single_line_text_field',
                'value': '130cm',
            })

    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_product_metafields_from_shopify')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_metafield_definitions_from_shopify')
    def test_pull_and_sync_metafield_definitions_and_values(self, mock_fetch_defs, mock_fetch_product_mfs):
        """Verify Pull/Sync fetches Shopify definitions, creates/updates them in Odoo,
        auto-populates to products, and maps product values from Shopify using Namespace + Key."""
        # 1. Setup products and offers
        prod1 = self.Template.create({'name': 'Product One'})
        prod2 = self.Template.create({'name': 'Product Two'})

        # Map to Shopify product IDs
        self.Offer.create({
            'sku': 'SKU-PROD-1',
            'name': 'Product One Variant',
            'ecommerce_account_id': self.account.id,
            'matched_product_id': prod1.product_variant_id.id,
            'ec_product_template_identifier': '1000000001',
        })
        self.Offer.create({
            'sku': 'SKU-PROD-2',
            'name': 'Product Two Variant',
            'ecommerce_account_id': self.account.id,
            'matched_product_id': prod2.product_variant_id.id,
            'ec_product_template_identifier': '1000000002',
        })

        # Pre-existing local definition that will be updated
        existing_defn = self.Definition.create({
            'name': 'Old Care Name',
            'namespace': 'custom',
            'key': 'care',
            'type': 'single_line_text_field',
            'description': 'Old description',
            'shopify_definition_id': 'def_gid_111',
            'ecommerce_account_id': self.account.id,
        })

        # Mock Shopify definitions:
        # 1 updated definition (care), 1 new definition (dimensions)
        mock_fetch_defs.return_value = [
            {
                'shopify_definition_id': 'def_gid_111',
                'name': 'Garment Care',
                'namespace': 'custom',
                'key': 'care',
                'description': 'Updated care instructions from Shopify',
                'owner_type': 'PRODUCT',
                'type': 'single_line_text_field',
            },
            {
                'shopify_definition_id': 'def_gid_222',
                'name': 'Dimensions',
                'namespace': 'custom',
                'key': 'dimensions',
                'description': 'Item dimensions',
                'owner_type': 'PRODUCT',
                'type': 'single_line_text_field',
            },
        ]

        # Mock Shopify product metafield values
        def mock_fetch_product_mfs_impl(account, shopify_product_id):
            if shopify_product_id == '1000000001':
                return [
                    {
                        'id': 'mf_val_101',
                        'namespace': 'custom',
                        'key': 'care',
                        'value': 'Hand wash only',
                        'type': 'single_line_text_field',
                    },
                    {
                        'id': 'mf_val_102',
                        'namespace': 'custom',
                        'key': 'dimensions',
                        'value': '10x20x5 cm',
                        'type': 'single_line_text_field',
                    },
                ]
            elif shopify_product_id == '1000000002':
                return [
                    {
                        'id': 'mf_val_201',
                        'namespace': 'custom',
                        'key': 'care',
                        'value': 'Dry clean only',
                        'type': 'single_line_text_field',
                    },
                ]
            return []

        mock_fetch_product_mfs.side_effect = mock_fetch_product_mfs_impl

        # Run Pull/Sync
        result = self.account.action_fetch_shopify_metafield_definitions()
        self.assertEqual(result.get('params', {}).get('type'), 'success')

        # Verify definitions in Odoo
        existing_defn.invalidate_recordset(['name', 'description'])
        self.assertEqual(existing_defn.name, 'Garment Care')
        self.assertEqual(existing_defn.description, 'Updated care instructions from Shopify')

        dim_defn = self.Definition.search([
            ('shopify_definition_id', '=', 'def_gid_222'),
            ('ecommerce_account_id', '=', self.account.id),
        ])
        self.assertTrue(dim_defn)
        self.assertEqual(dim_defn.name, 'Dimensions')
        self.assertEqual(dim_defn.namespace, 'custom')
        self.assertEqual(dim_defn.key, 'dimensions')

        # Verify Product 1 metafield values
        p1_care = self.ProductMetafield.search([
            ('product_tmpl_id', '=', prod1.id),
            ('definition_id', '=', existing_defn.id),
        ])
        self.assertEqual(p1_care.value, 'Hand wash only')
        self.assertEqual(p1_care.shopify_metafield_id, 'mf_val_101')
        self.assertEqual(p1_care.last_sync_status, 'success')
        self.assertEqual(p1_care.last_sync_direction, 'SHOPIFY_TO_ODOO')

        p1_dim = self.ProductMetafield.search([
            ('product_tmpl_id', '=', prod1.id),
            ('definition_id', '=', dim_defn.id),
        ])
        self.assertEqual(p1_dim.value, '10x20x5 cm')
        self.assertEqual(p1_dim.shopify_metafield_id, 'mf_val_102')

        # Verify Product 2 metafield values
        p2_care = self.ProductMetafield.search([
            ('product_tmpl_id', '=', prod2.id),
            ('definition_id', '=', existing_defn.id),
        ])
        self.assertEqual(p2_care.value, 'Dry clean only')
        self.assertEqual(p2_care.shopify_metafield_id, 'mf_val_201')

        p2_dim = self.ProductMetafield.search([
            ('product_tmpl_id', '=', prod2.id),
            ('definition_id', '=', dim_defn.id),
        ])
        # Product 2 has the definition available, but value is empty since Shopify had no value for it
        self.assertTrue(p2_dim)
        self.assertEqual(p2_dim.value, '')
        self.assertFalse(p2_dim.shopify_metafield_id)

        # No duplicate definitions were created
        care_defs = self.Definition.search([
            ('ecommerce_account_id', '=', self.account.id),
            ('namespace', '=', 'custom'),
            ('key', '=', 'care'),
        ])
        self.assertEqual(len(care_defs), 1)

        # No duplicate product metafields for Product 1
        p1_all_care = self.ProductMetafield.search([
            ('product_tmpl_id', '=', prod1.id),
            ('namespace', '=', 'custom'),
            ('key', '=', 'care'),
        ])
        self.assertEqual(len(p1_all_care), 1)

    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_metafield_definitions_from_shopify')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_product_metafields_from_shopify')
    def test_product_template_action_refresh_metafield_definitions_mapped(
        self, mock_fetch_product_mfs, mock_fetch_definitions
    ):
        """Test clicking Refresh Definitions on a mapped product:
        - Fetches definitions from Shopify via GraphQL
        - Creates/updates centralized definitions without duplicates
        - Populates definitions onto all products
        - Pulls product metafield values for the mapped product
        - Preserves existing valid values on other products
        - Returns reload action to refresh the product form tab immediately
        """
        prod_mapped = self.Template.create({'name': 'Mapped Hoodie'})
        prod_other = self.Template.create({'name': 'Other Product'})

        # Map prod_mapped to Shopify product 555666
        self.Offer.create({
            'sku': 'SKU-HOODIE',
            'name': 'Mapped Hoodie Variant',
            'matched_product_template_id': prod_mapped.id,
            'matched_product_id': prod_mapped.product_variant_id.id,
            'ecommerce_account_id': self.account.id,
            'ec_product_template_identifier': '555666',
        })

        # Pre-existing local value on other product
        existing_def = self.Definition.create({
            'name': 'Fabric',
            'namespace': 'custom',
            'key': 'fabric',
            'type': 'single_line_text_field',
            'ecommerce_account_id': self.account.id,
        })
        other_fabric = self.ProductMetafield.search([
            ('product_tmpl_id', '=', prod_other.id),
            ('definition_id', '=', existing_def.id),
        ])
        other_fabric.write({'value': 'Linen'})

        # Mock definitions returned by Shopify
        mock_fetch_definitions.return_value = [
            {
                'shopify_definition_id': 'def_gid_fabric',
                'name': 'Fabric',
                'namespace': 'custom',
                'key': 'fabric',
                'description': 'Product fabric composition',
                'owner_type': 'PRODUCT',
                'type': 'single_line_text_field',
            },
            {
                'shopify_definition_id': 'def_gid_size_guide',
                'name': 'Size Guide',
                'namespace': 'custom',
                'key': 'size_guide',
                'description': 'Size chart URL or text',
                'owner_type': 'PRODUCT',
                'type': 'single_line_text_field',
            },
        ]

        # Mock product values returned by Shopify for 555666
        mock_fetch_product_mfs.return_value = [
            {
                'id': 'mf_val_555_fabric',
                'namespace': 'custom',
                'key': 'fabric',
                'value': '80% Cotton 20% Polyester',
                'type': 'single_line_text_field',
            },
        ]

        # Call Refresh Definitions from the product form
        action = prod_mapped.action_refresh_metafield_definitions()

        # Check action returned
        self.assertEqual(action.get('type'), 'ir.actions.client')
        self.assertEqual(action.get('tag'), 'display_notification')
        self.assertEqual(action.get('params', {}).get('next'), {'type': 'ir.actions.client', 'tag': 'reload'})
        self.assertEqual(action.get('params', {}).get('type'), 'success')

        # Verify definitions exist in Odoo
        size_guide_def = self.Definition.search([
            ('namespace', '=', 'custom'),
            ('key', '=', 'size_guide'),
            ('ecommerce_account_id', '=', self.account.id),
        ])
        self.assertTrue(size_guide_def)
        self.assertEqual(size_guide_def.shopify_definition_id, 'def_gid_size_guide')

        # Existing def was updated with shopify_definition_id
        existing_def.invalidate_recordset(['shopify_definition_id', 'description'])
        self.assertEqual(existing_def.shopify_definition_id, 'def_gid_fabric')
        self.assertEqual(existing_def.description, 'Product fabric composition')

        # Verify mapped product values
        prod_mapped_fabric = self.ProductMetafield.search([
            ('product_tmpl_id', '=', prod_mapped.id),
            ('definition_id', '=', existing_def.id),
        ])
        self.assertEqual(prod_mapped_fabric.value, '80% Cotton 20% Polyester')
        self.assertEqual(prod_mapped_fabric.shopify_metafield_id, 'mf_val_555_fabric')

        prod_mapped_size_guide = self.ProductMetafield.search([
            ('product_tmpl_id', '=', prod_mapped.id),
            ('definition_id', '=', size_guide_def.id),
        ])
        self.assertTrue(prod_mapped_size_guide)
        self.assertEqual(prod_mapped_size_guide.value, '')

        # Verify other product still has its existing value untouched!
        other_fabric.invalidate_recordset(['value'])
        self.assertEqual(other_fabric.value, 'Linen')

        # Verify size_guide definition is also available on other product
        other_size_guide = self.ProductMetafield.search([
            ('product_tmpl_id', '=', prod_other.id),
            ('definition_id', '=', size_guide_def.id),
        ])
        self.assertTrue(other_size_guide)
        self.assertEqual(other_size_guide.value, '')

    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_metafield_definitions_from_shopify')
    def test_product_template_action_refresh_metafield_definitions_unmapped(self, mock_fetch_definitions):
        """Test clicking Refresh Definitions on an unmapped product:
        - Finds connected account
        - Fetches definitions from Shopify
        - Populates definitions onto the unmapped product template with blank values
        - Returns client reload action
        """
        prod_unmapped = self.Template.create({'name': 'Unmapped Product'})

        mock_fetch_definitions.return_value = [
            {
                'shopify_definition_id': 'def_gid_weight_spec',
                'name': 'Weight Spec',
                'namespace': 'custom',
                'key': 'weight_spec',
                'description': 'Product weight spec',
                'owner_type': 'PRODUCT',
                'type': 'single_line_text_field',
            },
        ]

        action = prod_unmapped.action_refresh_metafield_definitions()
        self.assertEqual(action.get('params', {}).get('next'), {'type': 'ir.actions.client', 'tag': 'reload'})

        mf = self.ProductMetafield.search([
            ('product_tmpl_id', '=', prod_unmapped.id),
            ('namespace', '=', 'custom'),
            ('key', '=', 'weight_spec'),
        ])
        self.assertTrue(mf)
        self.assertEqual(mf.value, '')

    def test_product_template_action_refresh_no_connected_account(self):
        """When no Shopify account is connected, UserError is raised."""
        self.account.state = 'disconnected'
        prod = self.Template.create({'name': 'Some Product'})
        with self.assertRaises(UserError):
            prod.action_refresh_metafield_definitions()

    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_metafield_definitions_from_shopify')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_definition_mirror_to_shopify_creates_and_saves_id(self, mock_api, mock_fetch):
        """Mirroring an unmirrored definition creates it on Shopify and stores shopify_definition_id."""
        mock_fetch.return_value = []
        mock_api.return_value = {
            'data': {
                'metafieldDefinitionCreate': {
                    'createdDefinition': {
                        'id': 'gid://shopify/MetafieldDefinition/98765',
                        'name': 'Washing Instructions',
                        'namespace': 'custom',
                        'key': 'washing_instructions',
                    },
                    'userErrors': [],
                },
            },
        }
        defn = self.Definition.create({
            'name': 'Washing Instructions',
            'namespace': 'custom',
            'key': 'washing_instructions',
            'type': 'single_line_text_field',
            'ecommerce_account_id': self.account.id,
        })
        self.assertFalse(defn.shopify_definition_id)

        action = defn.action_mirror_to_shopify()
        self.assertEqual(action.get('params', {}).get('type'), 'success')
        self.assertEqual(defn.shopify_definition_id, '98765')

    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_metafield_definitions_from_shopify')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_definition_mirror_reuses_when_already_exists_on_shopify(self, mock_api, mock_fetch):
        """When definition already exists on Shopify, reuse it instead of calling metafieldDefinitionCreate."""
        mock_fetch.return_value = [
            {
                'shopify_definition_id': 'gid://shopify/MetafieldDefinition/555444',
                'name': 'Washing Instructions',
                'namespace': 'custom',
                'key': 'washing_instructions',
                'owner_type': 'PRODUCT',
                'type': 'single_line_text_field',
            }
        ]
        defn = self.Definition.create({
            'name': 'Washing Instructions',
            'namespace': 'custom',
            'key': 'washing_instructions',
            'type': 'single_line_text_field',
            'ecommerce_account_id': self.account.id,
        })

        action = defn.action_mirror_to_shopify()
        self.assertEqual(action.get('params', {}).get('type'), 'success')
        self.assertEqual(defn.shopify_definition_id, '555444')
        self.assertEqual(mock_api.call_count, 0)

    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_product_metafields_from_shopify')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.fetch_metafield_definitions_from_shopify')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_idempotent_sync_multiple_products_shared_definition(self, mock_api, mock_fetch_defs, mock_fetch_prod_mfs):
        """Test that multiple products can share the same definition with different values,
        and multiple Pull + Push cycles run idempotently with zero duplicate definitions."""
        mock_fetch_prod_mfs.return_value = []
        prod1 = self.Template.create({'name': 'T-Shirt White'})
        prod2 = self.Template.create({'name': 'T-Shirt Black'})

        self.Offer.create({
            'sku': 'TSHIRT-W',
            'name': 'White',
            'ecommerce_account_id': self.account.id,
            'matched_product_id': prod1.product_variant_id.id,
            'ec_product_template_identifier': '101',
        })
        self.Offer.create({
            'sku': 'TSHIRT-B',
            'name': 'Black',
            'ecommerce_account_id': self.account.id,
            'matched_product_id': prod2.product_variant_id.id,
            'ec_product_template_identifier': '102',
        })

        mock_fetch_defs.return_value = [
            {
                'shopify_definition_id': 'gid://shopify/MetafieldDefinition/888',
                'name': 'Care Tag',
                'namespace': 'custom',
                'key': 'care_tag',
                'owner_type': 'PRODUCT',
                'type': 'single_line_text_field',
                'description': 'Care instructions tag',
            }
        ]

        # 1. Pull definitions first time
        self.account.action_fetch_shopify_metafield_definitions()

        # Only 1 definition created
        defs = self.Definition.search([('ecommerce_account_id', '=', self.account.id)])
        self.assertEqual(len(defs), 1)
        self.assertEqual(defs.key, 'care_tag')
        self.assertEqual(defs.shopify_definition_id, '888')

        # Both products automatically have a metafield row for this definition
        mf1 = self.ProductMetafield.search([
            ('product_tmpl_id', '=', prod1.id),
            ('definition_id', '=', defs.id),
        ])
        mf2 = self.ProductMetafield.search([
            ('product_tmpl_id', '=', prod2.id),
            ('definition_id', '=', defs.id),
        ])
        self.assertEqual(len(mf1), 1)
        self.assertEqual(len(mf2), 1)

        # Set different values for each product
        mf1.write({'value': 'Cold Machine Wash'})
        mf2.write({'value': 'Hand Wash Only'})

        # 2. Pull definitions second time (should be completely idempotent)
        self.account.action_fetch_shopify_metafield_definitions()

        # Still only 1 definition
        defs_after = self.Definition.search([('ecommerce_account_id', '=', self.account.id)])
        self.assertEqual(len(defs_after), 1)

        # Values on products preserved, not deleted or duplicated
        mf1_after = self.ProductMetafield.search([('product_tmpl_id', '=', prod1.id)])
        mf2_after = self.ProductMetafield.search([('product_tmpl_id', '=', prod2.id)])
        self.assertEqual(len(mf1_after), 1)
        self.assertEqual(len(mf2_after), 1)
        self.assertEqual(mf1.value, 'Cold Machine Wash')
        self.assertEqual(mf2.value, 'Hand Wash Only')

        # 3. Push product 1 metafields to Shopify
        from odoo.addons.ecommerce_shopify_upsync import utils_upsync
        mock_api.return_value = {
            'data': {
                'metafieldsSet': {
                    'metafields': [{'id': 'gid://shopify/Metafield/9991', 'namespace': 'custom', 'key': 'care_tag'}],
                    'userErrors': [],
                }
            }
        }
        res1 = utils_upsync.push_product_metafields_to_shopify(self.account, '101', [mf1])
        self.assertEqual(res1['errors'], [])

        # 4. Push product 2 metafields to Shopify
        mock_api.return_value = {
            'data': {
                'metafieldsSet': {
                    'metafields': [{'id': 'gid://shopify/Metafield/9992', 'namespace': 'custom', 'key': 'care_tag'}],
                    'userErrors': [],
                }
            }
        }
        res2 = utils_upsync.push_product_metafields_to_shopify(self.account, '102', [mf2])
        self.assertEqual(res2['errors'], [])

        # Verify that push NEVER called metafieldDefinitionCreate
        def_calls = [c for c in mock_api.call_args_list if 'metafieldDefinitionCreate' in c.args[2]]
        self.assertEqual(len(def_calls), 0)

        # Still strictly 1 definition in Odoo
        defs_final = self.Definition.search([('ecommerce_account_id', '=', self.account.id)])
        self.assertEqual(len(defs_final), 1)

