from odoo import models, fields, api


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    benefit_count = fields.Integer(
        string='Benefit Count', compute='_compute_benefit_count'
    )
    benefit_manager_id = fields.Many2one(
        'res.users', string='Benefit',groups="hr.group_hr_user",
        domain="[('share', '=', False), ('company_ids', 'in', company_id)]",
        help="User responsible for approving benefit requests of this employee."
    )

    def _compute_benefit_count(self):
        for employee in self:
            employee.benefit_count = self.env['benefit.request'].search_count([
                ('employee_id', '=', employee.id)
            ])

    def action_view_employee_benefits(self):
        self.ensure_one()
        return {
            'name': 'Benefits',
            'type': 'ir.actions.act_window',
            'res_model': 'benefit.request',
            'view_mode': 'list,form',
            'domain': [('employee_id', '=', self.id)],
            'context': {'default_employee_id': self.id},
        }
