from odoo import models, fields, api,_
from odoo.exceptions import UserError, ValidationError
STAGE_STATE_MAP = {
    'new': 'New',
    'maintenance': 'Under Maintenance',
    'ready': 'Ready to Deliver',
    'done': 'Done',
}


class WarrantyClaim(models.Model):
    _name = 'warranty.claim'
    _description = 'Warranty Claim'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    @api.constrains('warranty_id')
    def _check_sale_order_delivery(self):
        for claim in self:
            sale_order = claim.warranty_id.sale_order_id

            if not sale_order:
                continue

            done_delivery = sale_order.picking_ids.filtered(
                lambda p:
                p.picking_type_code == 'outgoing'
                and p.state == 'done'
            )

            if not done_delivery:
                raise ValidationError(
                    _(
                        "Warranty Claim cannot be created because the "
                        "delivery for Sale Order %s is not completed."
                    ) % sale_order.name
                )
    # Header information
    sale_order_id = fields.Many2one(
        'sale.order',
        string='Sale Order',
        readonly=True
    )

    name = fields.Char(string='Claim Subject', required=True, copy=False, readonly=True, default='New', tracking=True)
    warranty_id = fields.Many2one('product.warranty', string='Warranty', required=True)
    claim_date = fields.Datetime(string='Claim Date', default=fields.Datetime.now)
    responsible_id = fields.Many2one('res.users', string="Responsible", default=lambda self: self.env.user)
    priority = fields.Selection([('0', 'Normal'), ('1', 'Low'), ('2', 'High'), ('3', 'Very High'),], string="Priority", default='0')
    team_id = fields.Many2one('warranty.team', string='Warranty Team', compute='_compute_from_warranty', store=True, readonly=False)
    deadline = fields.Date(string='Deadline', required=True)
    stage_id = fields.Many2one('warranty.stage', string='Stage')

    # Editable fields - auto fill from warranty
    partner_id = fields.Many2one(
        'res.partner', string="Customer",
        compute='_compute_from_warranty', store=True, readonly=False
    )
    phone = fields.Char(
        string="Phone",
        compute='_compute_from_warranty', store=True, readonly=False
    )
    email = fields.Char(
        string="Email",
        compute='_compute_from_warranty', store=True, readonly=False
    )
    product_id = fields.Many2one(
        'product.product', string="Product",
        compute='_compute_from_warranty', store=True, readonly=False
    )
    serial_number = fields.Char(
        string="Serial No",
        compute='_compute_from_warranty', store=True, readonly=False
    )

    # Issue details
    trouble_responsible = fields.Char(string="Trouble Responsible")
    category_id = fields.Many2one('warranty.category', string="Category")
    root_cause = fields.Text(string="Root Causes")
    description = fields.Text(string='Issue Description', required=True)

    # Claim status
    state = fields.Selection([
        ('new', 'New'),
        ('maintenance', 'Under Maintenance'),
        ('ready', 'Ready to Deliver'),
        ('done', 'Done'),
    ], string="Status", default='new', tracking=True)

    # Warranty info sync
    @api.depends('warranty_id')
    def _compute_from_warranty(self):
        for rec in self:
            if rec.warranty_id:
                rec.partner_id = rec.warranty_id.partner_id
                rec.phone = rec.warranty_id.phone
                rec.email = rec.warranty_id.email
                rec.product_id = rec.warranty_id.product_id
                rec.serial_number = rec.warranty_id.serial_number
                rec.team_id = rec.warranty_id.warranty_team_id
            else:
                rec.partner_id = False
                rec.phone = False
                rec.email = False
                rec.product_id = False
                rec.serial_number = False
                rec.team_id = False

    # Sequence generation
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('warranty.claim') or 'New'
        return super().create(vals_list)

    # Stage change trigger
    @api.onchange('stage_id')
    def _onchange_stage_id(self):
        if self.stage_id:
            for state_key, stage_name in STAGE_STATE_MAP.items():
                if self.stage_id.name == stage_name:
                    self.state = state_key
                    break

    # State update helper
    def _sync_stage_from_state(self):
        stage_name = STAGE_STATE_MAP.get(self.state)
        if stage_name:
            stage = self.env['warranty.stage'].search([('name', '=', stage_name)], limit=1)
            if stage:
                self.stage_id = stage.id

    # State management actions
    def action_submit_claim(self):
        self.write({'state': 'maintenance'})
        self._sync_stage_from_state()

    def action_mark_ready(self):
        self.write({'state': 'ready'})
        self._sync_stage_from_state()

    def action_mark_done(self):
        self.write({'state': 'done'})
        self._sync_stage_from_state()
