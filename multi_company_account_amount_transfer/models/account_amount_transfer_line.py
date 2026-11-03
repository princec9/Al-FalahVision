# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import UserError

class AccountAmountTransferLine(models.Model):
    _name = 'account.amount.transfer.line'
    _description = 'Account Amount Transfer Line'
    _inherit = "analytic.mixin"
    _order = "sequence"

    transfer_id = fields.Many2one('account.amount.transfer', string='Transfer ID')
    name = fields.Char(string='Remarks')
    date = fields.Date(string='Date', related='transfer_id.date')
    partner_id = fields.Many2one('res.partner', string='Partner', ondelete='restrict')
    company_id = fields.Many2one('res.company', string='Company')
    journal = fields.Many2one('account.journal', string='Journal')
    account = fields.Many2one('account.account', string='Account')
    # asset_category_id = fields.Many2one('account.asset.category',
    #                                     string='Asset Category')
    debit = fields.Monetary(string='Debit', default=0.0, currency_field='currency_id')
    credit = fields.Monetary(string='Credit', default=0.0, currency_field='currency_id')
    journal_item_id = fields.One2many('account.move.line', 'account_amount_transfer_line_id', string='Journal Item')
    # vehicle_id = fields.Many2one('fleet.vehicle', string="Vehicle")
    # employee_name = fields.Many2one('hr.employee',string="Employee")
    # customer_segment_id = fields.Many2one('customer.segment', string="Customer Segment")
    sequence = fields.Integer(string='Sequence', default=10)
    # tax_tag_ids = fields.Many2many(
    #     string="Tags",
    #     comodel_name='account.account.tag',
    #     ondelete='restrict',
    #     context={'active_test': False},
    #     tracking=True,
    # )

    # analytic_distribution = fields.Json(
    # )
    # analytic_tag_ids = fields.Many2many('account.analytic.tag', string='Analytic Tags',
    #                                     compute="_compute_analytic_tag_ids", store=True, readonly=False,
    #                                     check_company=True, copy=True)
    currency_id = fields.Many2one('res.currency', string='Currency', related='company_id.currency_id')
    state = fields.Selection(selection=[
        ('draft', 'Draft'),
        ('posted', 'Posted'),
        ('cancel', 'Cancelled'),
    ], string='Status', readonly=True, related='transfer_id.state')

    # @api.onchange('analytic_distribution')
    # def _inverse_analytic_distribution(self):
    #     """ Unlink and recreate analytic_lines when modifying the distribution."""
    #     lines_to_modify = self.env['account.amount.transfer.line'].browse([
    #         line.id for line in self if line.state == "posted"
    #     ])
    #     lines_to_modify.analytic_line_ids.unlink()
    #     lines_to_modify._create_analytic_lines()

    # @api.depends('account', 'partner_id', 'date')
    # def _compute_analytic_tag_ids(self):
    #     for record in self:
    #         rec = self.env['account.analytic.default'].account_get(
    #             partner_id=record.partner_id.commercial_partner_id.id,
    #             account_id=record.account.id,
    #             user_id=record.env.uid,
    #             date=record.date,
    #             company_id=record.company_id.id
    #         )
    #         if rec:
    #             record.analytic_tag_ids = rec.analytic_tag_ids

    # @api.depends('account', 'partner_id', 'date')
    # def _compute_analytic_account_id(self):
    #     for record in self:
    #         rec = self.env['account.analytic.default'].account_get(
    #             partner_id=record.partner_id.commercial_partner_id.id,
    #             account_id=record.account.id,
    #             user_id=record.env.uid,
    #             date=record.date,
    #             company_id=record.company_id.id
    #         )
    #         if rec:
    #             record.analytic_account_id = rec.analytic_id

    @api.onchange('company_id')
    def _onchange_company(self):
        if self.account:
            self.account = False
        if self.journal:
            self.journal = False

    def prepare_move_values(self):
        values = {
            'move_type': 'entry',
            'ref': self.transfer_id.ref,
            'account_amount_transfer_id': self.transfer_id.id,
            'date': self.transfer_id.date,
            'journal_id': self.journal.id,
            'line_ids': [],
            'company_id': self.company_id.id,
            'currency_id': self.currency_id.id,

        }
        return values

    def prepare_move_line_values(self):
        values = {
            'name': self.name,
            'partner_id': self.partner_id.id,
            'account_id': self.account.id,
            'debit': self.debit,
            'credit': self.credit,
            'account_amount_transfer_line_id': self.id,
            'analytic_distribution': self.analytic_distribution,
            'currency_id': self.currency_id.id,
            # 'category_id': self.asset_category_id.id,
            # 'vehicle_id': self.vehicle_id.id,
            # 'customer_segment_id': self.customer_segment_id.id,
            # 'employee_name': self.employee_name.id

        }
        return values

    @api.model
    def default_get(self, default_fields):
        values = super(AccountAmountTransferLine, self).default_get(default_fields)
        if self._context.get('transfer_lines') and any(field_name in default_fields for field_name in ('debit', 'credit')):
            move = self.env['account.amount.transfer'].new({'transfer_lines': self._context['transfer_lines']})
            balance = sum(line['debit'] - line['credit'] for line in move.transfer_lines)
            if balance < 0.0:
                values.update({'debit': -balance})
            if balance > 0.0:
                values.update({'credit': balance})
        return values

    def _onchange_balance(self):
        for line in self:
            if line.currency_id == line.company_id.currency_id:
                balance = line.debit - line.credit
                line.debit = balance if balance > 0.0 else 0.0
                line.credit = -balance if balance < 0.0 else 0.0
            else:
                continue

    @api.onchange('debit')
    def _onchange_debit(self):
        if self.debit:
            self.credit = 0.0
        self._onchange_balance()

    @api.onchange('credit')
    def _onchange_credit(self):
        if self.credit:
            self.debit = 0.0
        self._onchange_balance()

    @api.onchange('journal')
    def _onchange_journal(self):
        if self.journal:
            transfer_id = self.transfer_id


            company = transfer_id.transfer_lines.filtered(lambda line: line.company_id.id == self.company_id.id and
                                    line.id != self.id).mapped('journal')


            if len(company) > 1:
                raise UserError(_('You can not different journal than other lines of the same company.'))

    def unlink(self):
        for line in self:
            if line.journal_item_id:
                line.journal_item_id.unlink()
        return super().unlink()