from odoo import models, fields, api
from odoo.exceptions import ValidationError
from dateutil.relativedelta import relativedelta


class ProductWarranty(models.Model):
    _name = 'product.warranty'
    _description = 'Product Warranty'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    # Warranty fields
    name = fields.Char(string='Warranty Name', required=False, default='New Warranty')
    # product_id = fields.Many2one('product.product', string='Product', tracking=True, required=True)
    product_id = fields.Many2one(
        'product.product',
        string='Product',
        tracking=True,
        required=True,
        domain="[('id', 'in', allowed_product_ids)]",
    )

    allowed_product_ids = fields.Many2many(
        'product.product',
        compute='_compute_allowed_product_ids',
        string='Allowed Products',
    )

    @api.depends('sale_order_id', 'purchase_order_id')
    def _compute_allowed_product_ids(self):
        for warranty in self:
            products = self.env['product.product']

            if warranty.sale_order_id:
                products = warranty.sale_order_id.order_line.mapped('product_id')

            elif warranty.purchase_order_id:
                products = warranty.purchase_order_id.order_line.mapped('product_id')

            warranty.allowed_product_ids = products
    warranty_period = fields.Integer(string='Warranty Months')
    start_date = fields.Date(string='Start Date', related='warranty_start_date', store=True)
    serial_number = fields.Char(string='Serial Number', tracking=True)
    # serial_number = fields.Char(string='Serial Number', tracking=True, required=True)
    sale_order_id = fields.Many2one('sale.order', string='Sale Order')
    invoice_id = fields.Many2one('account.move', string="Invoice")

    partner_id = fields.Many2one(
        'res.partner', string="Partner",
        compute="_compute_partner", store=True, readonly=False, tracking=True, required=True
    )
    phone = fields.Char(string="Phone", compute='_compute_contact_info', store=True, readonly=False)
    email = fields.Char(string="Email", compute='_compute_contact_info', store=True, readonly=False)

    receipt_no = fields.Char(string="Receipt No", readonly=True, copy=False, default='New')
    warranty_start_date = fields.Date(string='Warranty Start Date', tracking=True, required=True)
    warranty_end_date = fields.Date(string='Warranty End Date', tracking=True)
    salesperson_id = fields.Many2one(
        'res.users', string='Salesperson',
        related='sale_order_id.user_id', store=True, readonly=False
    )

    history_ids = fields.One2many('warranty.history', 'warranty_id', string="Warranty History")
    claim_ids = fields.One2many('product.warranty.claim', 'warranty_id', string="Claim History")
    warranty_claim_ids = fields.One2many('warranty.claim', 'warranty_id', string="Warranty Claims")

    warranty_team_id = fields.Many2one('warranty.team', string="Warranty Team")
    tag_ids = fields.Many2many('warranty.tag', string="Tags")
    model_no = fields.Char(string="Model No")
    merchant = fields.Char(string="Merchant")
    renewal_count = fields.Integer(string="No. of Renew")
    comment = fields.Text(string="Comment")
    purchase_order_id = fields.Many2one('purchase.order', string="Purchase Order", ondelete='cascade')

    warranty_type = fields.Selection([
        ('free', 'Free'),
        ('paid', 'Paid')
    ], string="Warranty Type", default='free', tracking=True, required=True)

    state = fields.Selection([
        ('new', 'New'),
        ('draft', 'Under Warranty'),
        ('to_invoice', 'To Be Invoice'),
        ('invoiced', 'Invoiced'),
        ('to_renew', 'To Be Renew'),
        ('expired', 'Expired')
    ], string='Status', default='new', tracking=True)

    claim_count = fields.Integer(string='Claims', compute='_compute_claim_count')
    invoice_count = fields.Integer(string='Invoices', compute='_compute_invoice_count')
    mail_sent = fields.Boolean(string="Mail Sent", default=False)

    # Computations
    @api.depends('sale_order_id', 'purchase_order_id')
    def _compute_partner(self):
        for rec in self:
            if rec.sale_order_id:
                rec.partner_id = rec.sale_order_id.partner_id
            elif rec.purchase_order_id:
                rec.partner_id = rec.purchase_order_id.partner_id
            else:
                if not rec.partner_id:
                    rec.partner_id = False

    @api.depends('partner_id', 'sale_order_id', 'purchase_order_id')
    def _compute_contact_info(self):
        for rec in self:
            if rec.sale_order_id:
                rec.phone = rec.sale_order_id.partner_id.phone
                rec.email = rec.sale_order_id.partner_id.email
            elif rec.purchase_order_id:
                rec.phone = rec.purchase_order_id.partner_id.phone
                rec.email = rec.purchase_order_id.partner_id.email
            elif rec.partner_id:
                rec.phone = rec.partner_id.phone
                rec.email = rec.partner_id.email
            else:
                rec.phone = False
                rec.email = False

    def _compute_claim_count(self):
        for rec in self:
            rec.claim_count = self.env['warranty.claim'].search_count(
                [('warranty_id', '=', rec.id)]
            )

    def _compute_invoice_count(self):
        for rec in self:
            rec.invoice_count = self.env['account.move'].search_count(
                [('id', '=', rec.invoice_id.id), ('move_type', '=', 'out_invoice')]
            ) if rec.invoice_id else 0

    # Warranty Logic
    def _get_warranty_months(self):
        # Returns warranty period based on "Apply Warranty On" setting
        self.ensure_one()
        apply_on = self.env['ir.config_parameter'].get_param(
            'is_product_warranty.apply_warranty_on', 'product'
        )
        product = self.product_id

        if apply_on == 'product_variant':
            return product.warranty_period or self.warranty_period or 12
        elif apply_on == 'product_category':
            return product.categ_id.warranty_period or self.warranty_period or 12
        else:
            return product.product_tmpl_id.warranty_period or self.warranty_period or 12

    def _get_renewal_cost(self):
        # Returns renewal cost based on "Apply Warranty On" setting
        self.ensure_one()
        apply_on = self.env['ir.config_parameter'].get_param(
            'is_product_warranty.apply_warranty_on', 'product'
        )
        product = self.product_id

        if apply_on == 'product_variant':
            return product.warranty_renewal_cost or 0
        elif apply_on == 'product_category':
            return product.categ_id.warranty_renewal_cost or 0
        else:
            return product.product_tmpl_id.warranty_renewal_cost or 0

    # Warranty Creation
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            serial = vals.get('serial_number')
            if serial and serial != 'N/A':
                existing = self.search([
                    ('serial_number', '=', serial),
                    ('state', 'not in', ['expired'])
                ])
                if existing:
                    raise ValidationError(
                        "You Cannot Create more than one Warranty with same serial No. "
                        "Please Renew existing Warranty."
                    )
            if vals.get('receipt_no', 'New') == 'New':
                vals['receipt_no'] = self.env['ir.sequence'].next_by_code(
                    'product.warranty.receipt'
                ) or 'DRFT'
            if not vals.get('name') or vals.get('name') == 'New Warranty':
                vals['name'] = f"Warranty/{vals['receipt_no']}"
        return super(ProductWarranty, self).create(vals_list)

    # UI Actions
    def action_view_claims(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Warranty Claims',
            'res_model': 'warranty.claim',
            'view_mode': 'list,form',
            'domain': [('warranty_id', '=', self.id)],
            'context': {'default_warranty_id': self.id},
            'target': 'current',
        }

    def action_view_invoice(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Invoice',
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': self.invoice_id.id,
            'target': 'current',
        }

    def action_activate(self):
        self.write({'state': 'draft'})

    def action_expire(self):
        self.write({'state': 'expired'})
    
    def action_send_renew_mail(self):
        self.ensure_one()
        # System Parameter ID 
        template_id = int(self.env['ir.config_parameter'].get_param(
            'is_product_warranty.warranty_renew_mail_template_id', 0
        ))
        
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'mail.compose.message',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_model': 'product.warranty',
                'default_res_ids': [self.id],
                'default_template_id': template_id,
                'default_composition_mode': 'comment',
                'default_use_template': True, 
                'default_subject': f"Warranty Renewal Confirmation - {self.receipt_no}",
            },
        }

    def action_send_registration_mail(self):
        self.ensure_one()
        template_id = int(self.env['ir.config_parameter'].get_param(
            'is_product_warranty.warranty_registration_mail_template_id', 0
        ))
        
        subject = f"Warranty Registration Confirmation - {self.receipt_no}"
        self.write({'mail_sent': True})
        
        ctx = {
            'default_model': 'product.warranty',
            'default_res_ids': [self.id],
            'default_template_id': template_id,
            'default_composition_mode': 'comment',
            'default_use_template': True,
            'default_subject': subject, 
        }
        
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'mail.compose.message',
            'view_mode': 'form',
            'target': 'new',
            'context': ctx,
        }

    def action_confirm(self):
        for rec in self:
            # Get warranty months from setting (Product / Variant / Category)
            warranty_months = rec._get_warranty_months()
            start_date = rec.warranty_start_date or fields.Date.today()
            end_date = start_date + relativedelta(months=warranty_months)

            if rec.warranty_type == 'paid':
                invoice = self.env['account.move'].create({
                    'move_type': 'out_invoice',
                    'partner_id': rec.partner_id.id,
                    'invoice_line_ids': [(0, 0, {
                        'name': f"Warranty: {rec.receipt_no}",
                        'quantity': 1,
                        'price_unit': rec._get_renewal_cost(),
                    })],
                })
                rec.write({
                    'state': 'to_invoice',
                    'warranty_start_date': start_date,
                    'warranty_end_date': end_date,
                    'warranty_period': warranty_months,
                    'invoice_id': invoice.id,
                })
            else:
                rec.write({
                    'state': 'draft',
                    'warranty_start_date': start_date,
                    'warranty_end_date': end_date,
                    'warranty_period': warranty_months,
                })

    def name_get(self):
        res = []
        for record in self:
            name = record.receipt_no or record.name
            res.append((record.id, name))
        return res

    # External API
    @api.model
    def _create_warranty_from_document(self, partner, product, source_field, source_record, trigger_type):
        config_value = self.env['ir.config_parameter'].get_param(
            'is_product_warranty.create_warranty_from', 'manual'
        )
        if config_value != trigger_type:
            return False

        warranty_months = product.product_tmpl_id.warranty_period or 12
        start_date = fields.Date.today()
        end_date = start_date + relativedelta(months=warranty_months)

        vals = {
            'name': product.display_name,
            'product_id': product.id,
            'partner_id': partner.id,
            'warranty_start_date': start_date,
            'warranty_end_date': end_date,
            'warranty_period': warranty_months,
            'state': 'draft',
        }
        vals.update(source_field)
        return self.create(vals)

    # Cron Job
    @api.model
    def _cron_update_warranty_states(self):
        today = fields.Date.today()

        expired = self.search([
            ('warranty_end_date', '<', today),
            ('state', 'not in', ['expired', 'new'])
        ])
        expired.write({'state': 'expired'})

        renew_limit = today + relativedelta(days=7)
        to_renew = self.search([
            ('warranty_end_date', '>=', today),
            ('warranty_end_date', '<=', renew_limit),
            ('state', 'not in', ['to_renew', 'expired', 'new'])
        ])
        to_renew.write({'state': 'to_renew'})

        invoice_limit = today + relativedelta(days=30)
        to_invoice = self.search([
            ('warranty_end_date', '>=', today),
            ('warranty_end_date', '<=', invoice_limit),
            ('warranty_type', '=', 'paid'),
            ('state', '=', 'draft')
        ])
        to_invoice.write({'state': 'to_invoice'})

        renew_template_id = int(self.env['ir.config_parameter'].get_param(
            'is_product_warranty.warranty_renew_mail_template_id', 0
        ))
        if renew_template_id:
            template = self.env['mail.template'].browse(renew_template_id)
            for warranty in to_renew:
                if warranty.email:
                    template.send_mail(warranty.id, force_send=True)


class WarrantyHistory(models.Model):
    _name = 'warranty.history'
    _description = 'Warranty History'

    warranty_id = fields.Many2one('product.warranty', string="Warranty", ondelete='cascade')
    date = fields.Date(string="Date")
    warranty_start_date = fields.Date(string="Warranty Start Date")
    warranty_end_date = fields.Date(string="Warranty End Date")
    renewal_amount = fields.Float(string="Renewal Amount")
    free_amount = fields.Float(string="Free")
    paid_amount = fields.Float(string="Paid")


class WarrantyClaim(models.Model):
    _name = 'product.warranty.claim'
    _description = 'Warranty Claim'

    warranty_id = fields.Many2one('product.warranty', string="Warranty", ondelete='cascade')
    name = fields.Char(string="Claim Reference", required=True)
    claim_date = fields.Date(string="Claim Date")
    description = fields.Text(string="Issue Description")

    state = fields.Selection([
        ('draft', 'Draft'),
        ('progress', 'In Progress'),
        ('done', 'Resolved'),
        ('cancel', 'Cancelled')
    ], default='draft')
    