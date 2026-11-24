from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    intercompany_journal_id = fields.Many2one(
        "account.journal",
        string="Intercompany Journal",
        related="company_id.intercompany_journal_id",
        readonly=False,
        domain="[('type', '=', 'general')]",
        help="Journal to use for intercompany journal entries.",
    )
    intercompany_income_account_id = fields.Many2one(
        "account.account",
        string="Intercompany Income Account",
        related="company_id.intercompany_income_account_id",
        readonly=False,
        domain="[('company_ids', 'in', [company_id]), ('account_type', 'in', ('income', 'income_other'))]",
        help="Default income account for intercompany journal entries without a product.",
    )
    intercompany_expense_account_id = fields.Many2one(
        "account.account",
        string="Intercompany Expense Account",
        related="company_id.intercompany_expense_account_id",
        readonly=False,
        domain="[('company_ids', 'in', [company_id]), ('account_type', 'in', ('expense', 'expense_deprec', 'expense_direct_cost'))]",
        help="Default expense account for intercompany journal entries without a product.",
    )