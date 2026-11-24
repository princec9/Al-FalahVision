from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    intercompany_journal_id = fields.Many2one(
        "account.journal",
        string="Intercompany Journal",
        domain="[('type', '=', 'general')]",
        help="Journal to use for intercompany journal entries.",
    )
    intercompany_income_account_id = fields.Many2one(
        "account.account",
        string="Intercompany Income Account",
        domain="[('company_ids', 'in', [id]), ('account_type', 'in', ('income', 'income_other'))]",
        help="Default income account for intercompany journal entries without a product.",
    )
    intercompany_expense_account_id = fields.Many2one(
        "account.account",
        string="Intercompany Expense Account",
        domain="[('company_ids', 'in', [id]), ('account_type', 'in', ('expense', 'expense_deprec', 'expense_direct_cost'))]",
        help="Default expense account for intercompany journal entries without a product.",
    )