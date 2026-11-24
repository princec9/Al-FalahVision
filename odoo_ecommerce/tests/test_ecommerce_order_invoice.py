# Part of Odoo. See LICENSE file for full copyright and licensing details.
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestEcommerceOrderInvoice(TransactionCase):
    """Test the automatic invoice creation triggered by a confirmed E-commerce payment.

    See `ecommerce.account._auto_create_invoice_and_payment` and the invoice-related branch of
    `ecommerce.account._process_order_data`.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company

        cls.sales_journal = cls.env['account.journal'].search([
            ('type', '=', 'sale'), ('company_id', '=', cls.company.id),
        ], limit=1) or cls.env['account.journal'].create({
            'name': 'Test E-commerce Sales Journal', 'type': 'sale', 'code': 'TESJ', 'company_id': cls.company.id,
        })
        cls.bank_journal = cls.env['account.journal'].search([
            ('type', '=', 'bank'), ('company_id', '=', cls.company.id),
        ], limit=1) or cls.env['account.journal'].create({
            'name': 'Test E-commerce Bank Journal', 'type': 'bank', 'code': 'TEBJ', 'company_id': cls.company.id,
        })

        # This test company may have no Chart of Accounts configured (e.g. a bare test database).
        # Make sure the minimal accounts required to post a customer invoice exist, without relying
        # on any localization module being installed.
        cls.income_account = cls.env['account.account'].search([
            ('company_ids', 'in', cls.company.id), ('account_type', '=', 'income'),
        ], limit=1) or cls.env['account.account'].create({
            'name': 'Test E-commerce Income',
            'code': 'TEINC1',
            'account_type': 'income',
            'company_ids': [Command.link(cls.company.id)],
        })
        cls.receivable_account = cls.env['account.account'].search([
            ('company_ids', 'in', cls.company.id), ('account_type', '=', 'asset_receivable'),
        ], limit=1) or cls.env['account.account'].create({
            'name': 'Test E-commerce Receivable',
            'code': 'TEREC1',
            'account_type': 'asset_receivable',
            'reconcile': True,
            'company_ids': [Command.link(cls.company.id)],
        })
        # Used as the fallback receivable account for any partner auto-created during order sync
        # (e.g. the default customer created when no billing/shipping address is provided).
        cls.env['ir.default'].set(
            'res.partner', 'property_account_receivable_id', cls.receivable_account.id, company_id=cls.company.id,
        )
        # Without a Chart of Accounts, `account.payment` cannot resolve an outstanding account for
        # the bank journal; the company-level transfer account is its fallback.
        if not cls.company.transfer_account_id:
            cls.company.transfer_account_id = cls.env['account.account'].create({
                'name': 'Test E-commerce Transfer',
                'code': 'TETRF1',
                'account_type': 'asset_current',
                'reconcile': True,
                'company_ids': [Command.link(cls.company.id)],
            })

        cls.product = cls.env['product.product'].create({
            'name': 'Test Sync Product',
            'default_code': 'TEST-SKU-1',
            'type': 'consu',
            'list_price': 50.0,
            'taxes_id': [Command.clear()],
            'property_account_income_id': cls.income_account.id,
        })

        cls.channel = cls.env['ecommerce.channel'].create({
            'name': 'Test Invoice Channel',
            'code': 'test_invoice_channel',
        })
        cls.account = cls.env['ecommerce.account'].create({
            'name': 'Test Invoice Account',
            'ecommerce_channel_id': cls.channel.id,
            'company_id': cls.company.id,
            'fulfilled_by': 'ecommerce',
            'auto_create_invoice': True,
            'account_journal_id': cls.sales_journal.id,
            'payment_journal_id': cls.bank_journal.id,
        })
        cls.account.action_connect()

    # === HELPERS === #

    def _new_order_data(self, order_id, financial_status, payment_provider_codes=None, qty=2, price_unit=50.0):
        """Build order data mimicking a freshly-placed E-commerce order, for orders unknown to
        Odoo yet: this exercises the full `_create_order_from_data` pipeline."""
        reference = f"#{order_id}"
        total_amount = qty * price_unit
        return {
            'id': str(order_id),
            'reference': reference,
            'status': 'confirmed',
            'financial_status': financial_status,
            'date_order': '2024-01-01T10:00:00+00:00',
            'invoice_date': '2024-01-01T10:00:00+00:00',
            'tax_included': False,
            'total_amount': total_amount,
            'payment_provider_codes': payment_provider_codes if payment_provider_codes is not None else ['manual'],
            'fulfillments': [],
            'order_lines': [{
                'id': f'{order_id}-line-1',
                'product_data': {
                    'sku': 'TEST-SKU-1',
                    'name': 'Test Sync Product',
                    'ec_product_identifier': f'pid-{order_id}',
                    'ec_product_template_identifier': f'ptid-{order_id}',
                },
                'qty_ordered': qty,
                'price_unit': price_unit,
                'price_subtotal': total_amount,
                'tax_amount': 0.0,
            }],
            'discount_lines': [],
            'shipping_lines': [],
            'other_addresses': [],
        }

    def _resync_order_data(self, order_id, financial_status, payment_provider_codes=None):
        """Build order data for the re-synchronization of an order already known to Odoo: this
        skips order-line/address preparation and exercises only the invoice-creation branch."""
        return {
            'id': str(order_id),
            'reference': f"#{order_id}",
            'status': 'confirmed',
            'financial_status': financial_status,
            'invoice_date': '2024-01-01T10:00:00+00:00',
            'payment_provider_codes': payment_provider_codes if payment_provider_codes is not None else ['manual'],
            'fulfillments': [],
        }

    def _create_synced_order(self, order_id, qty=2, price_unit=50.0):
        """Directly create a confirmed sales order already linked to the test E-commerce account,
        as if a previous synchronization had already created it."""
        partner = self.env['res.partner'].create({'name': f'Test Customer {order_id}'})
        return self.env['sale.order'].with_company(self.company).create({
            'partner_id': partner.id,
            'company_id': self.company.id,
            'ecommerce_account_id': self.account.id,
            'ecommerce_order_identifier': str(order_id),
            'ec_order_ref': f'#{order_id}',
            'state': 'sale',
            'order_line': [Command.create({
                'product_id': self.product.id,
                'product_uom_qty': qty,
                'price_unit': price_unit,
                'tax_ids': [Command.clear()],
            })],
        })

    def _active_invoices(self, order):
        return order.invoice_ids.filtered(lambda move: move.state != 'cancel')

    # === TEST CASE 1: Successful Payment === #

    def test_successful_payment_creates_and_posts_invoice(self):
        order = self.account._process_order_data(self._new_order_data('T1001', 'PAID'))
        self.assertEqual(order.state, 'sale')

        invoices = self._active_invoices(order)
        self.assertEqual(len(invoices), 1)
        self.assertEqual(invoices.state, 'posted')
        self.assertAlmostEqual(invoices.amount_total, 100.0)
        self.assertIn('#T1001', invoices.payment_reference or '')
        self.assertIn(invoices.payment_state, ('paid', 'in_payment'))

    # === TEST CASE 2: Pending Payment === #

    def test_pending_payment_creates_no_invoice(self):
        order = self.account._process_order_data(self._new_order_data('T1002', 'PENDING'))
        self.assertTrue(order)
        self.assertFalse(self._active_invoices(order))

    # === TEST CASE 3: Failed Payment === #

    def test_failed_payment_creates_no_invoice(self):
        order = self.account._process_order_data(self._new_order_data('T1003', 'VOIDED'))
        self.assertTrue(order)
        self.assertFalse(self._active_invoices(order))

    # === TEST CASE 4: Re-Synchronization === #

    def test_resync_paid_order_does_not_duplicate_invoice(self):
        order = self._create_synced_order('T1004')
        for _ in range(4):  # Simulate the same paid order being synced multiple times.
            self.account._process_order_data(self._resync_order_data('T1004', 'PAID'))

        invoices = self._active_invoices(order)
        self.assertEqual(len(invoices), 1)
        self.assertEqual(invoices.state, 'posted')
        self.assertIn(invoices.payment_state, ('paid', 'in_payment'))

    # === TEST CASE 5: Payment Updated Later === #

    def test_payment_updated_later_triggers_invoice(self):
        order = self._create_synced_order('T1005')
        self.account._process_order_data(self._resync_order_data('T1005', 'PENDING'))
        self.assertFalse(self._active_invoices(order))

        self.account._process_order_data(self._resync_order_data('T1005', 'PAID'))
        invoices = self._active_invoices(order)
        self.assertEqual(len(invoices), 1)
        self.assertEqual(invoices.state, 'posted')
        self.assertIn(invoices.payment_state, ('paid', 'in_payment'))

    # === TEST CASE 6: Invoice Creation Failure === #

    def test_invoice_creation_failure_is_logged_and_retried(self):
        order = self._create_synced_order('T1006')
        order_data = self._resync_order_data('T1006', 'PAID')

        SaleOrderModel = type(self.env['sale.order'])
        with patch.object(SaleOrderModel, '_create_invoices', side_effect=UserError('Simulated invoice failure')):
            processed_order = self.account._process_order_data(order_data)

        # The sales order sync itself must not be rolled back by the invoicing failure.
        self.assertEqual(processed_order, order)
        self.assertFalse(self._active_invoices(order))

        # `log_xml` writes through its own, separately-committed cursor (by design, so the log
        # survives even if the caller's transaction later rolls back). Read it back through a
        # fresh cursor too, since the test's own transaction snapshot predates that commit.
        with self.registry.cursor() as verify_cr:
            verify_cr.execute(
                "SELECT id FROM ir_logging WHERE func = %s AND message LIKE %s",
                ('_process_order_data', '%T1006%'),
            )
            self.assertTrue(verify_cr.fetchone(), "The invoice creation failure should have been logged.")

        # The next synchronization retries invoice creation without any duplicate.
        self.account._process_order_data(order_data)
        invoices = self._active_invoices(order)
        self.assertEqual(len(invoices), 1)
        self.assertEqual(invoices.state, 'posted')

    # === Extra: draft invoice from an interrupted previous attempt is posted on retry === #

    def test_existing_draft_invoice_is_posted_instead_of_duplicated(self):
        order = self._create_synced_order('T1007')
        draft_invoice = order._create_invoices()
        self.assertEqual(draft_invoice.state, 'draft')

        self.account._process_order_data(self._resync_order_data('T1007', 'PAID'))

        invoices = self._active_invoices(order)
        self.assertEqual(len(invoices), 1)
        self.assertEqual(invoices, draft_invoice)
        self.assertEqual(invoices.state, 'posted')
        self.assertIn(invoices.payment_state, ('paid', 'in_payment'))

    # === TEST CASE 7: E-commerce Adjustment Line === #

    def test_order_with_adjustment_line_creates_invoice_matching_shopify_total(self):
        """When an order has a rounding discrepancy, an adjustment line is added.
        The invoice must still be auto-created and posted, and its total must match Shopify."""
        order_data = self._new_order_data('T1008', 'PAID', qty=2, price_unit=500.0)
        # Line subtotal = 1000.0, but Shopify total is 1140.0 (e.g. 140 discrepancy/tax/rounding)
        order_data['total_amount'] = 1140.0

        order = self.account._process_order_data(order_data)
        self.assertTrue(order)

        # Confirm adjustment line was created
        adjustment_product = self.account._find_matching_product(
            '', 'ecommerce_default_adjustment', "E-commerce Adjustment", 'service',
        )
        self.assertTrue(order.order_line.filtered(lambda l: l.product_id.id == adjustment_product.id))
        self.assertAlmostEqual(order.amount_total, 1140.0)

        # Invoice MUST be created, posted, and match Shopify total 1140.0
        invoices = self._active_invoices(order)
        self.assertEqual(len(invoices), 1, "Invoice must be created even when order has an adjustment line.")
        self.assertEqual(invoices.state, 'posted')
        self.assertAlmostEqual(invoices.amount_total, 1140.0)

    # === TEST CASE 8: Products with 'delivery' Invoice Policy === #

    def test_order_with_delivery_policy_creates_invoice(self):
        """Products with 'delivery' invoicing policy must be invoiced on ordered quantities
        when paid via E-commerce, even if not delivered yet."""
        delivery_product = self.env['product.product'].create({
            'name': 'Test Delivery Policy Product',
            'default_code': 'TEST-SKU-DELIV',
            'type': 'consu',
            'invoice_policy': 'delivery',
            'list_price': 75.0,
            'taxes_id': [Command.clear()],
            'property_account_income_id': self.income_account.id,
        })
        order_data = self._new_order_data('T1009', 'PAID', qty=2, price_unit=75.0)
        order_data['order_lines'][0]['product_data']['sku'] = 'TEST-SKU-DELIV'

        order = self.account._process_order_data(order_data)
        self.assertTrue(order)

        invoices = self._active_invoices(order)
        self.assertEqual(len(invoices), 1, "Invoice must be created for products with delivery invoice policy.")
        self.assertEqual(invoices.state, 'posted')
        self.assertAlmostEqual(invoices.amount_total, 150.0)

    # === TEST CASE 9: Multiple Payment Providers === #

    def test_order_with_multiple_payment_providers(self):
        """Orders paid with multiple payment methods must not block invoice creation."""
        order_data = self._new_order_data('T1010', 'PAID', payment_provider_codes=['bogus', 'gift_card'], qty=1, price_unit=200.0)
        order = self.account._process_order_data(order_data)

        invoices = self._active_invoices(order)
        self.assertEqual(len(invoices), 1, "Invoice must be created even if order has multiple payment provider codes.")
        self.assertEqual(invoices.state, 'posted')
        self.assertAlmostEqual(invoices.amount_total, 200.0)

    # === TEST CASE 10: Lowercase 'paid' Financial Status === #

    def test_lowercase_paid_creates_invoice(self):
        """Webhook payloads or API responses with lowercase 'paid' must trigger invoice creation."""
        order = self.account._process_order_data(self._new_order_data('T1011', 'paid'))
        invoices = self._active_invoices(order)
        self.assertEqual(len(invoices), 1)
        self.assertEqual(invoices.state, 'posted')
        self.assertAlmostEqual(invoices.amount_total, 100.0)

    # === TEST CASE 11: Existing Shopify Order Without Invoice Auto-Invoiced on Next Sync === #

    def test_existing_order_paid_without_invoice_auto_invoiced_on_resync(self):
        """If an existing Odoo Sales Order has Shopify Order ID, Payment status = Paid, and No invoice
        (e.g. order S00305 / #1008), the next sync must create and post the missing invoice automatically."""
        # Create confirmed sales order without invoice
        order = self._create_synced_order('T1008_EXISTING', qty=1, price_unit=1140.0)
        self.assertEqual(order.state, 'sale')
        self.assertFalse(self._active_invoices(order))

        # Re-synchronization with PAID status triggers invoice creation
        order_data = self._resync_order_data('T1008_EXISTING', 'PAID')
        order_data['reference'] = '#1008'
        processed_order = self.account._process_order_data(order_data)
        self.assertEqual(processed_order, order)

        invoices = self._active_invoices(order)
        self.assertEqual(len(invoices), 1, "Missing invoice must be created automatically on next sync.")
        self.assertEqual(invoices.state, 'posted')
        self.assertAlmostEqual(invoices.amount_total, 1140.0)

    # === TEST CASE 12: Verify Invoice Total Matches Shopify Total with Shipping and Line Items === #

    def test_verify_invoice_amount_matches_shopify_total_with_shipping(self):
        """Verify invoice amount: Shopify Total = Odoo Invoice Total including products and shipping."""
        order_data = self._new_order_data('T1012', 'PAID', qty=2, price_unit=150.0)
        order_data['shipping_lines'] = [{
            'id': 'ship-line-1',
            'title': 'Express Shipping',
            'carrier_code': 'express',
            'price_unit': 30.0,
            'price_subtotal': 30.0,
            'tax_amount': 0.0,
        }]
        order_data['total_amount'] = 330.0  # 300 products + 30 shipping

        order = self.account._process_order_data(order_data)
        invoices = self._active_invoices(order)
        self.assertEqual(len(invoices), 1)
        self.assertEqual(invoices.state, 'posted')
        self.assertAlmostEqual(invoices.amount_total, 330.0)
        self.assertEqual(len(invoices.invoice_line_ids), 2, "Invoice must contain both product line and shipping line.")
