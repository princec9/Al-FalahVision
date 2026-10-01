from odoo import _, api, models
from odoo.exceptions import UserError


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    @api.depends('company_id.sale_discount_approval')
    def _compute_discount_approval_required(self):
        # extra dependency so stored flags refresh when the setting is toggled
        return super()._compute_discount_approval_required()

    @api.onchange('discount', 'user_id')
    def _onchange_discount_approval(self):
        # skip "Salesperson Required" and "Approval Required" when approval is disabled
        if not self.company_id.sale_discount_approval:
            return
        return super()._onchange_discount_approval()

    def _needs_discount_approval(self):
        self.ensure_one()
        if not self.company_id.sale_discount_approval:
            return False
        return super()._needs_discount_approval()

    def action_open_discount_approval_wizard(self):
        self.ensure_one()
        if not self.company_id.sale_discount_approval:
            raise UserError(_('Salesperson discount approval is disabled for this company.'))
        return super().action_open_discount_approval_wizard()