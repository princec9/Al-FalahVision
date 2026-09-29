# Part of Odoo. See LICENSE file for full copyright and licensing details.
from unittest.mock import MagicMock, call, patch

from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from odoo.addons.ecommerce_shopify import const
from odoo.addons.ecommerce_shopify_upsync import utils_upsync


@tagged('post_install', '-at_install')
class TestShopifyAttributeVariantSync(TransactionCase):

    def setUp(self):
        super().setUp()
        self.company = self.env.company

        channel = self.env['ecommerce.channel'].search([('code', '=', 'shopify')], limit=1)
        if not channel:
            channel = self.env['ecommerce.channel'].create({
                'name': 'Shopify Test Channel',
                'code': 'shopify',
                'sequence': 40,
            })

        self.account = self.env['ecommerce.account'].create({
            'name': 'Test Shopify Variant Account',
            'company_id': self.company.id,
            'ecommerce_channel_id': channel.id,
            'fulfilled_by': 'ecommerce',
            'shopify_store': 'test-variant-store',
            'shopify_access_token': 'test-token',
            'state': 'connected',
        })

        self.cat = self.env['product.category'].create({'name': 'Apparel'})

        # Attributes
        self.attr_color = self.env['product.attribute'].create({
            'name': 'Color',
            'create_variant': 'always',
            'value_ids': [
                (0, 0, {'name': 'Black'}),
                (0, 0, {'name': 'Blue'}),
                (0, 0, {'name': 'Green'}),
                (0, 0, {'name': 'Navy'}),
            ],
        })
        self.val_black = self.attr_color.value_ids.filtered(lambda v: v.name == 'Black')
        self.val_blue = self.attr_color.value_ids.filtered(lambda v: v.name == 'Blue')
        self.val_green = self.attr_color.value_ids.filtered(lambda v: v.name == 'Green')
        self.val_navy = self.attr_color.value_ids.filtered(lambda v: v.name == 'Navy')

        self.attr_size = self.env['product.attribute'].create({
            'name': 'Size',
            'create_variant': 'always',
            'value_ids': [
                (0, 0, {'name': 'S'}),
                (0, 0, {'name': 'M'}),
                (0, 0, {'name': 'L'}),
            ],
        })
        self.val_s = self.attr_size.value_ids.filtered(lambda v: v.name == 'S')
        self.val_m = self.attr_size.value_ids.filtered(lambda v: v.name == 'M')
        self.val_l = self.attr_size.value_ids.filtered(lambda v: v.name == 'L')

        self.attr_material = self.env['product.attribute'].create({
            'name': 'Material',
            'create_variant': 'always',
            'value_ids': [
                (0, 0, {'name': 'Cotton'}),
                (0, 0, {'name': 'Wool'}),
            ],
        })
        self.val_cotton = self.attr_material.value_ids.filtered(lambda v: v.name == 'Cotton')
        self.val_wool = self.attr_material.value_ids.filtered(lambda v: v.name == 'Wool')

    def _create_offers_for_template(self, tmpl, sku_prefix='SKU'):
        offers = self.env['ecommerce.offer']
        for i, variant in enumerate(tmpl.product_variant_ids, start=1):
            if not variant.default_code:
                variant.default_code = f"{sku_prefix}-{i}"
            if not variant.barcode:
                variant.barcode = f"BAR-{sku_prefix}-{i}"
            offer = self.env['ecommerce.offer'].create({
                'name': variant.display_name,
                'sku': variant.default_code,
                'ecommerce_account_id': self.account.id,
                'matched_product_id': variant.id,
            })
            offers |= offer
        return offers

    def test_product_with_no_attributes(self):
        """Product with no attributes: single variant, no options sent."""
        tmpl = self.env['product.template'].create({
            'name': 'Simple T-Shirt No Attributes',
            'categ_id': self.cat.id,
            'default_code': 'SIMPLE-01',
            'barcode': 'BAR-SIMPLE-01',
            'list_price': 25.0,
        })
        offers = self._create_offers_for_template(tmpl, sku_prefix='SIMPLE')
        self.assertEqual(len(offers), 1)

        product_data = self.account._prepare_shopify_product_data(tmpl, offers)
        self.assertEqual(product_data['options'], [])
        self.assertEqual(product_data['options_detail'], [])
        self.assertEqual(len(product_data['variants']), 1)
        self.assertEqual(product_data['variants'][0]['variant_option_values'], [])
        self.assertEqual(product_data['variants'][0]['sku'], 'SIMPLE-01')
        self.assertEqual(product_data['variants'][0]['barcode'], 'BAR-SIMPLE-01')
        self.assertEqual(product_data['variants'][0]['price'], 25.0)

        # Initial Push: creates product and variant
        create_resp = {
            'shopify_product_id': '1001',
            'handle': 'simple-t-shirt-no-attributes',
            'variants': [{
                'shopify_variant_id': '2001',
                'shopify_inventory_item_id': '3001',
                'sku': 'SIMPLE-01',
            }],
        }
        with patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_product_to_shopify', return_value=create_resp) as mock_push, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.publish_product_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.models.ecommerce_account.ECommerceAccount._sync_product_collection_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.models.ecommerce_account.ECommerceAccount._push_product_images_to_shopify'):

            self.account._push_product_template_to_shopify(offers)
            self.assertEqual(offers[0].ec_product_template_identifier, '1001')
            self.assertEqual(offers[0].ec_product_identifier, '2001')

        # Re-Push: updates product and single variant without duplicate creation
        remote_info = {
            'options': [],
            'variants': [{
                'id': 'gid://shopify/ProductVariant/2001',
                'numeric_id': '2001',
                'sku': 'SIMPLE-01',
                'barcode': 'BAR-SIMPLE-01',
                'selectedOptions': [],
                'inventory_item_id': '3001',
            }],
        }
        with patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_product_to_shopify', return_value={'shopify_product_id': '1001', 'handle': 'simple-t-shirt-no-attributes'}), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.get_shopify_product_options_and_variants', return_value=remote_info), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_variant_to_shopify') as mock_push_variant, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.create_variants_on_shopify') as mock_create_variant, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.publish_product_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.models.ecommerce_account.ECommerceAccount._sync_product_collection_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.models.ecommerce_account.ECommerceAccount._push_product_images_to_shopify'):

            self.account._push_product_template_to_shopify(offers)
            mock_push_variant.assert_called_once()
            mock_create_variant.assert_not_called()
            self.assertEqual(offers[0].ec_product_identifier, '2001')

    def test_product_with_color_attribute(self):
        """Product with Color attribute: Color option with Black, Blue, Green values."""
        tmpl = self.env['product.template'].create({
            'name': 'Polo T-Shirt Color Only',
            'categ_id': self.cat.id,
            'attribute_line_ids': [
                (0, 0, {
                    'attribute_id': self.attr_color.id,
                    'value_ids': [(6, 0, [self.val_black.id, self.val_blue.id, self.val_green.id])],
                })
            ]
        })
        offers = self._create_offers_for_template(tmpl, sku_prefix='POLO-COL')
        self.assertEqual(len(offers), 3)

        product_data = self.account._prepare_shopify_product_data(tmpl, offers)
        self.assertEqual(product_data['options'], ['Color'])
        self.assertEqual(len(product_data['options_detail']), 1)
        self.assertEqual(product_data['options_detail'][0]['name'], 'Color')
        self.assertCountEqual(product_data['options_detail'][0]['values'], ['Black', 'Blue', 'Green'])

        # Check that variants have the correct optionValues
        for var in product_data['variants']:
            self.assertEqual(len(var['variant_option_values']), 1)
            self.assertEqual(var['variant_option_values'][0]['optionName'], 'Color')
            self.assertIn(var['variant_option_values'][0]['name'], ['Black', 'Blue', 'Green'])

        create_resp = {
            'shopify_product_id': '1002',
            'handle': 'polo-t-shirt-color-only',
            'variants': [
                {'shopify_variant_id': '201', 'shopify_inventory_item_id': '301', 'sku': offers[0].sku},
                {'shopify_variant_id': '202', 'shopify_inventory_item_id': '302', 'sku': offers[1].sku},
                {'shopify_variant_id': '203', 'shopify_inventory_item_id': '303', 'sku': offers[2].sku},
            ],
        }
        with patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_product_to_shopify', return_value=create_resp), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.publish_product_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.models.ecommerce_account.ECommerceAccount._sync_product_collection_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.models.ecommerce_account.ECommerceAccount._push_product_images_to_shopify'):

            self.account._push_product_template_to_shopify(offers)
            self.assertEqual(offers[0].ec_product_identifier, '201')
            self.assertEqual(offers[1].ec_product_identifier, '202')
            self.assertEqual(offers[2].ec_product_identifier, '203')

    def test_product_with_size_attribute(self):
        """Product with Size attribute: Size option with S, M, L values."""
        tmpl = self.env['product.template'].create({
            'name': 'Polo T-Shirt Size Only',
            'categ_id': self.cat.id,
            'attribute_line_ids': [
                (0, 0, {
                    'attribute_id': self.attr_size.id,
                    'value_ids': [(6, 0, [self.val_s.id, self.val_m.id, self.val_l.id])],
                })
            ]
        })
        offers = self._create_offers_for_template(tmpl, sku_prefix='POLO-SZ')
        self.assertEqual(len(offers), 3)

        product_data = self.account._prepare_shopify_product_data(tmpl, offers)
        self.assertEqual(product_data['options'], ['Size'])
        self.assertEqual(product_data['options_detail'][0]['name'], 'Size')
        self.assertCountEqual(product_data['options_detail'][0]['values'], ['S', 'M', 'L'])

        for var in product_data['variants']:
            self.assertEqual(len(var['variant_option_values']), 1)
            self.assertEqual(var['variant_option_values'][0]['optionName'], 'Size')
            self.assertIn(var['variant_option_values'][0]['name'], ['S', 'M', 'L'])

    def test_product_with_color_and_size(self):
        """Product with Color + Size: maintains option order and generates combinations."""
        tmpl = self.env['product.template'].create({
            'name': 'Polo T-Shirt Color and Size',
            'categ_id': self.cat.id,
            'attribute_line_ids': [
                (0, 0, {
                    'attribute_id': self.attr_color.id,
                    'value_ids': [(6, 0, [self.val_black.id, self.val_blue.id])],
                }),
                (0, 0, {
                    'attribute_id': self.attr_size.id,
                    'value_ids': [(6, 0, [self.val_s.id, self.val_m.id])],
                }),
            ]
        })
        offers = self._create_offers_for_template(tmpl, sku_prefix='POLO-CS')
        self.assertEqual(len(offers), 4)

        product_data = self.account._prepare_shopify_product_data(tmpl, offers)
        # Option order from Odoo attribute lines
        self.assertEqual(product_data['options'], ['Color', 'Size'])
        self.assertEqual(len(product_data['options_detail']), 2)
        self.assertEqual(product_data['options_detail'][0]['name'], 'Color')
        self.assertEqual(product_data['options_detail'][1]['name'], 'Size')

        # Each variant must have 2 option values in exact order
        for var in product_data['variants']:
            self.assertEqual(len(var['variant_option_values']), 2)
            self.assertEqual(var['variant_option_values'][0]['optionName'], 'Color')
            self.assertIn(var['variant_option_values'][0]['name'], ['Black', 'Blue'])
            self.assertEqual(var['variant_option_values'][1]['optionName'], 'Size')
            self.assertIn(var['variant_option_values'][1]['name'], ['S', 'M'])

    def test_multiple_variants_different_combinations_color_size_material(self):
        """Product with 3 attributes: Color + Size + Material."""
        tmpl = self.env['product.template'].create({
            'name': 'Polo Tri-Attribute',
            'categ_id': self.cat.id,
            'attribute_line_ids': [
                (0, 0, {
                    'attribute_id': self.attr_color.id,
                    'value_ids': [(6, 0, [self.val_black.id])],
                }),
                (0, 0, {
                    'attribute_id': self.attr_size.id,
                    'value_ids': [(6, 0, [self.val_m.id])],
                }),
                (0, 0, {
                    'attribute_id': self.attr_material.id,
                    'value_ids': [(6, 0, [self.val_cotton.id, self.val_wool.id])],
                }),
            ]
        })
        offers = self._create_offers_for_template(tmpl, sku_prefix='POLO-TRI')
        self.assertEqual(len(offers), 2)

        product_data = self.account._prepare_shopify_product_data(tmpl, offers)
        self.assertEqual(product_data['options'], ['Color', 'Size', 'Material'])
        self.assertEqual(len(product_data['options_detail']), 3)
        self.assertEqual(product_data['options_detail'][0]['name'], 'Color')
        self.assertEqual(product_data['options_detail'][1]['name'], 'Size')
        self.assertEqual(product_data['options_detail'][2]['name'], 'Material')

        for var in product_data['variants']:
            self.assertEqual(len(var['variant_option_values']), 3)
            self.assertEqual([o['optionName'] for o in var['variant_option_values']], ['Color', 'Size', 'Material'])

    def test_edit_existing_variant_and_push_again(self):
        """Edit an existing variant (price, barcode) and push again: updates variant, no duplicate created."""
        tmpl = self.env['product.template'].create({
            'name': 'Polo Edit Variant',
            'categ_id': self.cat.id,
            'attribute_line_ids': [
                (0, 0, {
                    'attribute_id': self.attr_color.id,
                    'value_ids': [(6, 0, [self.val_black.id, self.val_blue.id])],
                })
            ]
        })
        offers = self._create_offers_for_template(tmpl, sku_prefix='POLO-EDIT')
        offer_black = offers[0]
        offer_blue = offers[1]

        # Preset existing mappings
        for o in offers:
            o.ec_product_template_identifier = '8888'
        offer_black.ec_product_identifier = '9001'
        offer_blue.ec_product_identifier = '9002'

        # Edit variant Black in Odoo
        offer_black.matched_product_id.lst_price = 55.0
        offer_black.matched_product_id.barcode = 'BAR-EDITED-9001'

        remote_info = {
            'options': [{'id': 'gid://shopify/ProductOption/1', 'name': 'Color', 'values': ['Black', 'Blue']}],
            'variants': [
                {
                    'id': 'gid://shopify/ProductVariant/9001',
                    'numeric_id': '9001',
                    'sku': offer_black.sku,
                    'barcode': 'OLD-BARCODE',
                    'selectedOptions': [{'name': 'Color', 'value': 'Black'}],
                    'inventory_item_id': '7001',
                },
                {
                    'id': 'gid://shopify/ProductVariant/9002',
                    'numeric_id': '9002',
                    'sku': offer_blue.sku,
                    'barcode': offer_blue.matched_product_id.barcode,
                    'selectedOptions': [{'name': 'Color', 'value': 'Blue'}],
                    'inventory_item_id': '7002',
                },
            ],
        }

        with patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_product_to_shopify', return_value={'shopify_product_id': '8888', 'handle': 'polo-edit-variant'}) as mock_push_product, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.get_shopify_product_options_and_variants', return_value=remote_info), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_variant_to_shopify') as mock_push_variant, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.create_variants_on_shopify') as mock_create_variant, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.publish_product_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.models.ecommerce_account.ECommerceAccount._sync_product_collection_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.models.ecommerce_account.ECommerceAccount._push_product_images_to_shopify'):

            self.account._push_product_template_to_shopify(offers)

            # Verified productUpdate was used
            self.assertEqual(mock_push_product.call_args[1].get('shopify_product_id'), '8888')
            # Verified variants updated, none created
            self.assertEqual(mock_push_variant.call_count, 2)
            mock_create_variant.assert_not_called()

            # Check that Black variant update received updated price and barcode
            calls = mock_push_variant.call_args_list
            black_call = next(c for c in calls if c[0][2] == '9001')
            variant_data_sent = black_call[0][3]
            self.assertEqual(variant_data_sent['price'], 55.0)
            self.assertEqual(variant_data_sent['barcode'], 'BAR-EDITED-9001')

    def test_add_new_odoo_variant_and_push(self):
        """Add a new Odoo variant to existing product: updates option values, creates only new variant."""
        tmpl = self.env['product.template'].create({
            'name': 'Polo Add Variant',
            'categ_id': self.cat.id,
            'attribute_line_ids': [
                (0, 0, {
                    'attribute_id': self.attr_color.id,
                    'value_ids': [(6, 0, [self.val_black.id, self.val_blue.id])],
                })
            ]
        })
        existing_offers = self._create_offers_for_template(tmpl, sku_prefix='POLO-ADD')
        for o in existing_offers:
            o.ec_product_template_identifier = '8888'
        existing_offers[0].ec_product_identifier = '9001'
        existing_offers[1].ec_product_identifier = '9002'

        # Now add Green to attribute line in Odoo
        attr_line = tmpl.attribute_line_ids[0]
        attr_line.write({'value_ids': [(4, self.val_green.id)]})

        green_variant = tmpl.product_variant_ids.filtered(
            lambda v: 'Green' in v.product_template_attribute_value_ids.mapped('name')
        )

        self.assertTrue(green_variant)
        green_variant.default_code = 'POLO-ADD-3'
        offer_green = self.env['ecommerce.offer'].create({
            'name': green_variant.display_name,
            'sku': green_variant.default_code,
            'ecommerce_account_id': self.account.id,
            'matched_product_id': green_variant.id,
        })
        all_offers = existing_offers | offer_green

        remote_info = {
            'options': [{'id': 'gid://shopify/ProductOption/1', 'name': 'Color', 'values': ['Black', 'Blue']}],
            'variants': [
                {
                    'id': 'gid://shopify/ProductVariant/9001',
                    'numeric_id': '9001',
                    'sku': existing_offers[0].sku,
                    'selectedOptions': [{'name': 'Color', 'value': 'Black'}],
                    'inventory_item_id': '7001',
                },
                {
                    'id': 'gid://shopify/ProductVariant/9002',
                    'numeric_id': '9002',
                    'sku': existing_offers[1].sku,
                    'selectedOptions': [{'name': 'Color', 'value': 'Blue'}],
                    'inventory_item_id': '7002',
                },
            ],
        }

        new_variant_resp = [{
            'shopify_variant_id': '9003',
            'shopify_inventory_item_id': '7003',
            'sku': 'POLO-ADD-3',
            'selectedOptions': [{'name': 'Color', 'value': 'Green'}],
        }]

        with patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_product_to_shopify', return_value={'shopify_product_id': '8888', 'handle': 'polo-add-variant'}), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.get_shopify_product_options_and_variants', return_value=remote_info), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.sync_product_options_on_shopify') as mock_sync_options, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_variant_to_shopify') as mock_push_variant, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.create_variants_on_shopify', return_value=new_variant_resp) as mock_create_variant, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.publish_product_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.models.ecommerce_account.ECommerceAccount._sync_product_collection_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.models.ecommerce_account.ECommerceAccount._push_product_images_to_shopify'):

            self.account._push_product_template_to_shopify(all_offers)

            # Option sync was invoked to add Green
            mock_sync_options.assert_called_once()
            # Existing variants updated
            self.assertEqual(mock_push_variant.call_count, 2)
            # Only Green variant created
            mock_create_variant.assert_called_once()
            created_variants_input = mock_create_variant.call_args[0][2]
            self.assertEqual(len(created_variants_input), 1)
            self.assertEqual(created_variants_input[0]['sku'], 'POLO-ADD-3')

            # Green offer received its Shopify ID
            self.assertEqual(offer_green.ec_product_identifier, '9003')
            self.assertEqual(offer_green.shopify_inventory_item_id, '7003')

    def test_change_existing_variant_attribute_value_and_push(self):
        """Change an existing variant's attribute value (Blue -> Navy) and push: updates variant option values."""
        tmpl = self.env['product.template'].create({
            'name': 'Polo Change Value',
            'categ_id': self.cat.id,
            'attribute_line_ids': [
                (0, 0, {
                    'attribute_id': self.attr_color.id,
                    'value_ids': [(6, 0, [self.val_black.id, self.val_blue.id])],
                })
            ]
        })
        offers = self._create_offers_for_template(tmpl, sku_prefix='POLO-CHG')
        offer_black = offers[0]
        offer_blue = offers[1]

        offer_black.ec_product_template_identifier = '8888'
        offer_black.ec_product_identifier = '9001'
        offer_blue.ec_product_template_identifier = '8888'
        offer_blue.ec_product_identifier = '9002'

        # Change Blue to Navy in attribute line
        attr_line = tmpl.attribute_line_ids[0]
        attr_line.write({'value_ids': [(3, self.val_blue.id), (4, self.val_navy.id)]})

        remote_info = {
            'options': [{'id': 'gid://shopify/ProductOption/1', 'name': 'Color', 'values': ['Black', 'Blue']}],
            'variants': [
                {'id': 'gid://shopify/ProductVariant/9001', 'numeric_id': '9001', 'sku': offer_black.sku, 'selectedOptions': [{'name': 'Color', 'value': 'Black'}]},
                {'id': 'gid://shopify/ProductVariant/9002', 'numeric_id': '9002', 'sku': offer_blue.sku, 'selectedOptions': [{'name': 'Color', 'value': 'Blue'}]},
            ],
        }

        with patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_product_to_shopify', return_value={'shopify_product_id': '8888', 'handle': 'polo-change-value'}), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.get_shopify_product_options_and_variants', return_value=remote_info), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.sync_product_options_on_shopify') as mock_sync_options, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_variant_to_shopify') as mock_push_variant, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.create_variants_on_shopify') as mock_create_variant, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.publish_product_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.models.ecommerce_account.ECommerceAccount._sync_product_collection_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.models.ecommerce_account.ECommerceAccount._push_product_images_to_shopify'):

            self.account._push_product_template_to_shopify(offers)

            mock_sync_options.assert_called_once()
            mock_create_variant.assert_not_called()
            self.assertEqual(mock_push_variant.call_count, 2)

    def test_remove_odoo_variant_deletes_shopify_variant(self):
        """Remove an Odoo variant: deletes Shopify variant without affecting remaining variants."""
        tmpl = self.env['product.template'].create({
            'name': 'Polo Remove Variant',
            'categ_id': self.cat.id,
            'attribute_line_ids': [
                (0, 0, {
                    'attribute_id': self.attr_color.id,
                    'value_ids': [(6, 0, [self.val_black.id, self.val_blue.id])],
                })
            ]
        })
        offers = self._create_offers_for_template(tmpl, sku_prefix='POLO-REM')
        offer_black = offers[0]
        offer_blue = offers[1]

        offer_black.ec_product_template_identifier = '8888'
        offer_black.ec_product_identifier = '9001'
        offer_blue.ec_product_template_identifier = '8888'
        offer_blue.ec_product_identifier = '9002'

        # Shopify has 3 variants: 9001 (Black), 9002 (Blue), and an old 9003 (Green) that was removed in Odoo
        remote_info = {
            'options': [{'id': 'gid://shopify/ProductOption/1', 'name': 'Color', 'values': ['Black', 'Blue', 'Green']}],
            'variants': [
                {'id': 'gid://shopify/ProductVariant/9001', 'numeric_id': '9001', 'sku': offer_black.sku, 'selectedOptions': [{'name': 'Color', 'value': 'Black'}]},
                {'id': 'gid://shopify/ProductVariant/9002', 'numeric_id': '9002', 'sku': offer_blue.sku, 'selectedOptions': [{'name': 'Color', 'value': 'Blue'}]},
                {'id': 'gid://shopify/ProductVariant/9003', 'numeric_id': '9003', 'sku': 'OLD-GREEN-SKU', 'selectedOptions': [{'name': 'Color', 'value': 'Green'}]},
            ],
        }

        with patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_product_to_shopify', return_value={'shopify_product_id': '8888', 'handle': 'polo-remove-variant'}), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.get_shopify_product_options_and_variants', return_value=remote_info), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.delete_variants_from_shopify') as mock_delete_variants, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_variant_to_shopify') as mock_push_variant, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.create_variants_on_shopify') as mock_create_variant, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.publish_product_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.models.ecommerce_account.ECommerceAccount._sync_product_collection_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.models.ecommerce_account.ECommerceAccount._push_product_images_to_shopify'):

            self.account._push_product_template_to_shopify(offers)

            # Verified variant 9003 was deleted
            mock_delete_variants.assert_called_once_with(self.account, '8888', ['9003'])
            # Verified remaining variants 9001 and 9002 updated
            self.assertEqual(mock_push_variant.call_count, 2)
            mock_create_variant.assert_not_called()

    def test_unmapped_offer_reconciles_by_option_values_and_sku(self):
        """Unmapped offer reconciles by matching option values and SKU before creating anything."""
        tmpl = self.env['product.template'].create({
            'name': 'Polo Reconcile Unmapped',
            'categ_id': self.cat.id,
            'attribute_line_ids': [
                (0, 0, {
                    'attribute_id': self.attr_color.id,
                    'value_ids': [(6, 0, [self.val_black.id])],
                })
            ]
        })
        offers = self._create_offers_for_template(tmpl, sku_prefix='POLO-REC')
        offer = offers[0]
        offer.ec_product_template_identifier = '8888'
        offer.ec_product_identifier = False  # Mapping is missing!

        remote_info = {
            'options': [{'id': 'gid://shopify/ProductOption/1', 'name': 'Color', 'values': ['Black']}],
            'variants': [
                {
                    'id': 'gid://shopify/ProductVariant/7771',
                    'numeric_id': '7771',
                    'sku': offer.sku,
                    'selectedOptions': [{'name': 'Color', 'value': 'Black'}],
                    'inventory_item_id': '5551',
                },
            ],
        }

        with patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_product_to_shopify', return_value={'shopify_product_id': '8888', 'handle': 'polo-reconcile-unmapped'}), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.get_shopify_product_options_and_variants', return_value=remote_info), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_variant_to_shopify') as mock_push_variant, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.create_variants_on_shopify') as mock_create_variant, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.publish_product_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.models.ecommerce_account.ECommerceAccount._sync_product_collection_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.models.ecommerce_account.ECommerceAccount._push_product_images_to_shopify'):

            self.account._push_product_template_to_shopify(offers)

            # Mapping restored on offer
            self.assertEqual(offer.ec_product_identifier, '7771')
            self.assertEqual(offer.shopify_inventory_item_id, '5551')

            # Updated existing variant, did NOT create
            mock_push_variant.assert_called_once()
            mock_create_variant.assert_not_called()

    def test_graphql_option_values_mutation_builder(self):
        """Verify GraphQL mutation builder outputs optionValues correctly."""
        variants = [{
            'price': 29.99,
            'sku': 'TSHIRT-RED-L',
            'barcode': '1234567890',
            'compare_at_price': 39.99,
            'taxable': True,
            'inventory_management': True,
            'inventory_policy': 'DENY',
            'weight': 0.25,
            'weight_unit': 'KILOGRAMS',
            'variant_option_values': [
                {'optionName': 'Color', 'name': 'Red'},
                {'optionName': 'Size', 'name': 'L'},
            ],
        }]

        options_detail = [
            {'name': 'Color', 'values': ['Red', 'Blue']},
            {'name': 'Size', 'values': ['S', 'M', 'L']},
        ]

        options_input = utils_upsync._build_product_options_input([], options_detail=options_detail)
        self.assertIn('name: "Color"', options_input)
        self.assertIn('values: [{name: "Red"}, {name: "Blue"}]', options_input)
        self.assertIn('name: "Size"', options_input)
        self.assertIn('values: [{name: "S"}, {name: "M"}, {name: "L"}]', options_input)

        variants_input = utils_upsync._build_variants_input(variants)
        self.assertIn('optionValues: [{optionName: "Color", name: "Red"}, {optionName: "Size", name: "L"}]', variants_input)
        self.assertIn('sku: "TSHIRT-RED-L"', variants_input)

        mutation_update = utils_upsync._generate_variant_update_mutation('1001', '2001', variants[0])
        self.assertIn('optionValues: [{optionName: "Color", name: "Red"}, {optionName: "Size", name: "L"}]', mutation_update)
