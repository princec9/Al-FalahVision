from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    strict_percentage_validation = fields.Boolean(
        string='Strict Percentage Validation',
        config_parameter='bt_hr_intercompany_salary.strict_percentage_validation',
        default=True,
    )

    intercompany_payroll_credit_account_id = fields.Many2one(
        'account.account',
        string='Intercompany Reimbursement Account',
        domain="[('account_type', 'in', ['expense', 'income', 'income_other'])]",
    )
    intercompany_payroll_expense_account_id = fields.Many2one(
        'account.account',
        string='Intercompany Expense Account',
        domain="[('account_type', '=', 'expense')]",
    )
    intercompany_payroll_journal_id = fields.Many2one(
        'account.journal',
        string='Intercompany Payroll Journal',
        domain="[('type', '=', 'general')]",
    )

    def get_values(self):
        res = super().get_values()
        params = self.env['ir.config_parameter'].sudo()
        company_id = self.env.company.id
        credit_id = int(params.get_param(
            'bt_hr_intercompany_salary.credit_account_%d' % company_id, 0
        ))
        expense_id = int(params.get_param(
            'bt_hr_intercompany_salary.expense_account_%d' % company_id, 0
        ))
        journal_id = int(params.get_param(
            'bt_hr_intercompany_salary.payroll_journal_%d' % company_id, 0
        ))
        res.update({
            'intercompany_payroll_credit_account_id': credit_id or False,
            'intercompany_payroll_expense_account_id': expense_id or False,
            'intercompany_payroll_journal_id': journal_id or False,
        })
        return res

    def set_values(self):
        super().set_values()
        params = self.env['ir.config_parameter'].sudo()
        company_id = self.env.company.id
        params.set_param(
            'bt_hr_intercompany_salary.credit_account_%d' % company_id,
            self.intercompany_payroll_credit_account_id.id or 0
        )
        params.set_param(
            'bt_hr_intercompany_salary.expense_account_%d' % company_id,
            self.intercompany_payroll_expense_account_id.id or 0
        )
        params.set_param(
            'bt_hr_intercompany_salary.payroll_journal_%d' % company_id,
            self.intercompany_payroll_journal_id.id or 0
        )
