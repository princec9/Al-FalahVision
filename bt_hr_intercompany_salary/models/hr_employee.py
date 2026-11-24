import logging
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    multi_company_ids = fields.One2many(
        'employee.company.contribution',
        'employee_id',
        string='Company Contributions',
        help='Define how salary cost is split across companies.'
    )
    is_multi_company_employee = fields.Boolean(
        string='Multi-Company Employee',
        default=False,
        groups="hr.group_hr_user",
        help='Enable to activate intercompany salary cost allocation for this employee.'
    )
    total_contribution_percentage = fields.Float(
        string='Total Contribution (%)',
        compute='_compute_total_contribution_percentage',
        groups="hr.group_hr_user",
        store=True,
    )
    contribution_status = fields.Selection(
        selection=[
            ('valid', 'Valid (100%)'),
            ('incomplete', 'Incomplete (< 100%)'),
            ('excess', 'Excess (> 100%)'),
            ('empty', 'Not Configured'),
        ],
        compute='_compute_total_contribution_percentage',
        store=True,
        groups="hr.group_hr_user",
        string='Contribution Status',
    )

    @api.depends(
        'multi_company_ids',
        'multi_company_ids.contribution_percentage',
        'multi_company_ids.active',
    )
    def _compute_total_contribution_percentage(self):
        for employee in self:
            active_lines = employee.multi_company_ids.filtered('active')
            total = sum(active_lines.mapped('contribution_percentage'))
            employee.total_contribution_percentage = total

            if not active_lines:
                employee.contribution_status = 'empty'
            elif abs(total - 100.0) < 0.01:
                employee.contribution_status = 'valid'
            elif total < 100.0:
                employee.contribution_status = 'incomplete'
            else:
                employee.contribution_status = 'excess'

    @api.constrains('multi_company_ids', 'is_multi_company_employee')
    def _check_total_contribution(self):
        settings = self.env['ir.config_parameter'].sudo()
        strict_validation = settings.get_param(
            'bt_hr_intercompany_salary.strict_percentage_validation', True
        )
        if not strict_validation:
            return

        for employee in self:
            if not employee.is_multi_company_employee:
                continue
            active_lines = employee.multi_company_ids.filtered('active')
            if not active_lines:
                continue
            total = sum(active_lines.mapped('contribution_percentage'))
            if abs(total - 100.0) >= 0.01:
                raise ValidationError(_(
                    'Total active contribution percentage for employee %(name)s '
                    'must equal 100%%. Current total: %(total).2f%%',
                    name=employee.name,
                    total=total,
                ))

    def get_active_contributions(self):
        """Returns active contribution lines for this employee."""
        self.ensure_one()
        return self.multi_company_ids.filtered('active')

    def get_contributions_excluding_parent(self):
        """Returns active contribution lines excluding the employee's own company."""
        self.ensure_one()
        return self.get_active_contributions().filtered(
            lambda l: l.company_id != self.company_id
        )

    def action_open_contributions(self):
        """Smart button action to open company contributions for this employee."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Company Contributions',
            'res_model': 'employee.company.contribution',
            'view_mode': 'list,form',
            'domain': [('employee_id', '=', self.id)],
            'context': {'default_employee_id': self.id},
        }
