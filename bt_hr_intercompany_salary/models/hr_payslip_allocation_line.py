import logging
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class HrPayslipAllocationLine(models.Model):
    _name = 'hr.payslip.allocation.line'
    _description = 'Payslip Salary Allocation Line'
    _order = 'payslip_id, sequence'

    sequence = fields.Integer(string='Sequence', default=10)
    payslip_id = fields.Many2one(
        'hr.payslip', string='Payslip',
        required=True, ondelete='cascade', index=True,
    )
    company_id = fields.Many2one(
        'res.company', string='Company', required=True,
    )
    contribution_percentage = fields.Float(
        string='Contribution (%)', digits=(5, 2), required=True,
    )
    amount = fields.Monetary(
        string='Amount',
        currency_field='currency_id',
        help='Computed from total salary × contribution %. '
             'Click "Refresh Amounts" after salary computation to update.',
    )
    currency_id = fields.Many2one(
        'res.currency',
        related='payslip_id.company_id.currency_id',
        readonly=True,
    )
    is_parent_company = fields.Boolean(
        string='Parent Company',
        compute='_compute_is_parent_company',
        store=True,
    )
    invoice_id = fields.Many2one(
        'account.move', string='Journal Entry', readonly=True, copy=False,
    )
    intercompany_partner_id = fields.Many2one(
        'res.partner', string='Intercompany Partner',
        help='Partner for intercompany journal entry. Falls back to company partner.'
    )
    invoice_display = fields.Char(
        string='Journal Entry',
        compute='_compute_invoice_display',
    )

    @api.depends('invoice_id', 'invoice_id.state', 'invoice_id.name')
    def _compute_invoice_display(self):
        for rec in self:
            if rec.invoice_id:
                rec.invoice_display = (
                        rec.invoice_id.name
                        or _('Draft Journal Entry')
                )
            else:
                rec.invoice_display = False

    @api.depends('payslip_id.company_id', 'company_id')
    def _compute_is_parent_company(self):
        for line in self:
            line.is_parent_company = (
                line.company_id == line.payslip_id.company_id
            )

    @api.constrains('contribution_percentage')
    def _check_percentage(self):
        for rec in self:
            if rec.contribution_percentage < 0 or rec.contribution_percentage > 100:
                raise ValidationError(_(
                    'Contribution percentage must be between 0.01 and 100.'
                ))

    def recompute_amount(self):
        for line in self:
            total_salary = line.payslip_id._get_total_salary_amount()
            line.amount = round(
                total_salary * (line.contribution_percentage / 100.0), 2
            )

    def get_partner_for_invoice(self):
        self.ensure_one()
        return self.intercompany_partner_id or self.company_id.partner_id