from odoo import models, fields, api


class AccountReimbursementLine(models.Model):
    _name = "account.reimbursement.line"

    name = fields.Char("Label")
    account_id = fields.Many2one('account.account')
    move_id = fields.Many2one(
        comodel_name='account.move',
        string='Move',
        index=True,
        auto_join=True,
        ondelete="cascade",
        check_company=True,
    )
    journal_id = fields.Many2one(
        related='move_id.journal_id', store=True,
        index=True,
        copy=False,
    )
    company_id = fields.Many2one(
        related='move_id.company_id', store=True, readonly=True, precompute=True,
        index=True,
    )
    company_currency_id = fields.Many2one(
        string='Company Currency',
        related='move_id.company_currency_id', readonly=True, store=True, precompute=True,
    )
    currency_id = fields.Many2one('res.currency', related='move_id.currency_id')
    parent_state = fields.Selection(related='move_id.state', store=True)
    date = fields.Date(
        related='move_id.date', store=True,
        copy=False,
        aggregator='min',
    )
    invoice_date = fields.Date(
        related='move_id.invoice_date', store=True,
        copy=False,
        aggregator='min',
    )
    ref = fields.Char(
        related='move_id.ref', store=True,
        copy=False,
        index='trigram',
    )
    move_type = fields.Selection(related='move_id.move_type')
    tax_ids = fields.Many2many(
        comodel_name='account.tax',
        string="Taxes",
        compute='_compute_tax_ids', store=True, readonly=False, precompute=True,
        context={'active_test': False},
        check_company=True,
    )
    display_type = fields.Selection(
        selection=[
            ('product', 'Product'),
            ('cogs', 'Cost of Goods Sold'),
            ('tax', 'Tax'),
            ('discount', "Discount"),
            ('rounding', "Rounding"),
            ('payment_term', 'Payment Term'),
            ('line_section', 'Section'),
            ('line_note', 'Note'),
            ('epd', 'Early Payment Discount')
        ],
        compute='_compute_display_type', store=True, readonly=False, precompute=True,
        required=True,
    )
    product_id = fields.Many2one(
        comodel_name='product.product',
        string='Product',
        ondelete='restrict',
        check_company=True,
    )
    product_uom_id = fields.Many2one(
        comodel_name='uom.uom',
        string='Unit of Measure',
        compute='_compute_product_uom_id', store=True, readonly=False, precompute=True,
        domain="[('category_id', '=', product_uom_category_id)]",
        ondelete="restrict",
    )
    quantity = fields.Float(
        string='Quantity',
        compute='_compute_quantity', store=True, readonly=False, precompute=True,
        digits='Product Unit of Measure',
        help="The optional quantity expressed by this line, eg: number of product sold. "
             "The quantity is not a legal requirement but is very useful for some reports.",
    )
    # === Price fields === #
    price_unit = fields.Float(
        string='Unit Price',
        compute="_compute_price_unit", store=True, readonly=False, precompute=True,
        digits='Product Price',
    )
    price_subtotal = fields.Monetary(
        string='Subtotal',
        compute='_compute_totals', store=True,
        currency_field='currency_id',
    )
    price_total = fields.Monetary(
        string='Total',
        compute='_compute_totals', store=True,
        currency_field='currency_id',
    )
    discount = fields.Float(
        string='Discount (%)',
        digits='Discount',
        default=0.0,
    )
    discount_amount = fields.Monetary(
        string="Discount Amount",
        currency_field="currency_id",
        default=0.0,
    )
    is_imported = fields.Boolean()
    new_discount = fields.Float(
        string="Discount (%)",
        digits="Discount",
        default=0.0,
    )

    @api.onchange('new_discount', 'price_unit', 'quantity')
    def _onchange_new_discount(self):
        for line in self:
            total = line.price_unit * line.quantity
            line.discount_amount = total * line.new_discount / 100 if total else 0.0

    @api.onchange('discount_amount')
    def _onchange_discount_amount(self):
        for line in self:
            total = line.price_unit * line.quantity
            line.new_discount = (line.discount_amount / total) * 100 if total else 0.0
    # @api.onchange('price_unit')
    # def onchange_price_unit(self):
    #     for line in self:
    #         # Update discount based on list price
    #         if line.product_id.list_price > 0:
    #             line.new_discount = ((line.product_id.list_price - line.price_unit) / line.product_id.list_price) * 100
    #         else:
    #             line.new_discount = 0.0
    @api.depends('product_id', 'company_id', 'display_type')
    def _compute_tax_ids(self):
        for line in self:
            if line.display_type in ('line_section', 'line_note', 'payment_term'):
                continue
            # keep taxes typed by hand when there is no product
            if line.product_id or not line.tax_ids:
                line.tax_ids = line._get_computed_taxes()

    def _get_computed_taxes(self):
        """Same logic as the quotation / invoice line: product sale taxes of
        the company (or its closest parent), then the fiscal position."""
        self.ensure_one()
        taxes = self.product_id.taxes_id
        if not taxes and self.account_id.tax_ids:
            taxes = self.account_id.tax_ids
        company = self.company_id or self.move_id.company_id
        if taxes and company:
            if hasattr(taxes, '_filter_taxes_by_company'):
                taxes = taxes._filter_taxes_by_company(company)
            else:
                taxes = taxes.filtered(lambda t: t.company_id in company.parent_ids)
        fpos = self.move_id.fiscal_position_id
        if taxes and fpos:
            taxes = fpos.map_tax(taxes)
        return taxes

    @api.depends('quantity', 'discount','new_discount', 'price_unit', 'tax_ids', 'currency_id')
    def _compute_totals(self):
        """ Compute 'price_subtotal' / 'price_total' outside of `_sync_tax_lines` because those values must be visible for the
        user on the UI with draft moves and the dynamic lines are synchronized only when saving the record.
        """
        AccountTax = self.env['account.tax']
        for line in self:
            # TODO remove the need of cogs lines to have a price_subtotal/price_total
            if line.display_type not in ('product', 'cogs'):
                line.price_total = line.price_subtotal = False
                continue

            base_line = self.env['account.tax']._prepare_base_line_for_taxes_computation(
                line,
                price_unit=line.price_unit,
                quantity=line.quantity,
                discount=line.new_discount
                # discount=line.discount,
            )
            AccountTax._add_tax_details_in_base_line(base_line, line.company_id)
            line.price_subtotal = base_line['tax_details']['raw_total_excluded_currency']
            line.price_total = base_line['tax_details']['raw_total_included_currency']

    @api.depends('product_id', 'product_uom_id')
    def _compute_price_unit(self):
        for line in self:
            if not line.product_id or line.display_type in ('line_section', 'line_note') or line.is_imported:
                continue
            if line.move_id.is_sale_document(include_receipts=True):
                document_type = 'sale'
            elif line.move_id.is_purchase_document(include_receipts=True):
                document_type = 'purchase'
            else:
                document_type = 'other'
            line.price_unit = line.product_id._get_tax_included_unit_price(
                line.move_id.company_id,
                line.move_id.currency_id,
                line.move_id.date,
                document_type,
                fiscal_position=line.move_id.fiscal_position_id,
                product_uom=line.product_uom_id,
            )

    @api.depends('display_type')
    def _compute_quantity(self):
        for line in self:
            if line.display_type == 'product':
                line.quantity = line.quantity if line.quantity else 1
            else:
                line.quantity = False

    @api.depends('product_id')
    def _compute_product_uom_id(self):
        for line in self:
            # vendor bills should have the product purchase UOM
            if line.move_id.is_purchase_document():
                line.product_uom_id = line.product_id.uom_po_id
            else:
                line.product_uom_id = line.product_id.uom_id

    @api.depends('move_id')
    def _compute_display_type(self):
        for line in self.filtered(lambda l: not l.display_type):
            line.display_type = 'product'


