import logging
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class EmployeeCompanyContribution(models.Model):
    """
    Defines the percentage-based salary contribution split
    for an employee across multiple companies.
    """
    _name = 'employee.company.contribution'
    _description = 'Employee Company Contribution'
    _order = 'employee_id, sequence'
    _rec_name = 'company_id'

    sequence = fields.Integer(string='Sequence', default=10)
    employee_id = fields.Many2one(
        'hr.employee', string='Employee', required=True,
        ondelete='cascade', index=True,
    )
    company_id = fields.Many2one(
        'res.company', string='Company', required=True, index=True,
    )
    contribution_percentage = fields.Float(
        string='Contribution (%)', required=True, digits=(5, 2),
        help='Percentage of the salary cost borne by this company (0-100).'
    )
    intercompany_partner_id = fields.Many2one(
        'res.partner', string='Intercompany Partner',
        help='Partner for intercompany invoices. Falls back to company partner if not set.'
    )
    active = fields.Boolean(string='Active', default=True)
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id',
        string='Currency', readonly=True,
    )

    @api.constrains('contribution_percentage')
    def _check_contribution_percentage(self):
        for rec in self:
            if rec.contribution_percentage < 0 or rec.contribution_percentage > 100:
                raise ValidationError(_(
                    'Contribution percentage for %s must be between 0.01 and 100.',
                    rec.company_id.name
                ))

    @api.constrains('employee_id', 'company_id')
    def _check_unique_company(self):
        """Prevent duplicate company entries for the same employee."""
        for rec in self:
            domain = [
                ('employee_id', '=', rec.employee_id.id),
                ('company_id', '=', rec.company_id.id),
                ('id', '!=', rec.id),
            ]
            if self.search_count(domain):
                raise ValidationError(_(
                    'A contribution record for employee %(emp)s and company %(comp)s already exists.',
                    emp=rec.employee_id.name,
                    comp=rec.company_id.name,
                ))

    def get_partner_for_invoice(self):
        """Returns the partner for intercompany documents."""
        self.ensure_one()
        return self.intercompany_partner_id or self.company_id.partner_id
