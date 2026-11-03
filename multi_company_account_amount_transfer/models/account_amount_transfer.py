# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import UserError
from odoo.tools import float_compare

class AccountAmountTransfer(models.Model):
    _name = 'account.amount.transfer'
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = 'Account Amount Transfer'
    _rec_name = 'name'

    name = fields.Char(string='Name', default=lambda self: _("New"))
    ref = fields.Char(string='Reference')
    date = fields.Date(string='Date')
    journal_entry_ids = fields.One2many('account.move', 'account_amount_transfer_id', string='Journal Entries')
    entries_count = fields.Integer(string='Entries Count', compute='_compute_entry_ids', store=True)
    transfer_lines = fields.One2many(
        'account.amount.transfer.line', 'transfer_id',
        string='Transfer Lines', copy=True)
    state = fields.Selection(selection=[
        ('draft', 'Draft'),
        ('posted', 'Posted'),
        ('cancel', 'Cancelled'),
    ], string='Status', required=True, readonly=True, copy=False, tracking=True,
        default='draft')
    user_id = fields.Many2one('res.users', string='User', required=True, default=lambda self: self.env.user,
                              tracking=True)
    sequence = fields.Integer(string='Sequence', default=10)


    @api.depends('journal_entry_ids')
    def _compute_entry_ids(self):
        for entry in self:
            entry.entries_count = len(entry.journal_entry_ids)

    def check_journal_entry(self):
        journal_entries = self.journal_entry_ids.mapped('company_id')
        companies = self.transfer_lines.mapped('company_id')
        diff = list(set(companies).difference(journal_entries))
        if diff:
            return diff
        else:
            return False

    def post_internal_transfer(self):
        self.ensure_one()
        if self.state != 'draft':
            return

        self._check_company_balance()

        Move = self.env['account.move']
        for company in self.transfer_lines.mapped('company_id'):
            company_lines = self.transfer_lines.filtered(
                lambda l: l.company_id == company)
            entry = self.journal_entry_ids.filtered(
                lambda e: e.company_id == company)[:1]

            if not entry:
                vals = company_lines[0].prepare_move_values()
                vals['line_ids'] = [
                    (0, 0, l.prepare_move_line_values()) for l in company_lines]
                Move.with_company(company).create(vals)
            elif entry.state == 'draft':
                commands = []
                for line in company_lines:
                    values = line.prepare_move_line_values()
                    if line.journal_item_id:
                        commands.append((1, line.journal_item_id[0].id, values))
                    else:
                        commands.append((0, 0, values))
                entry.with_company(company).write({
                    'ref': self.ref,
                    'date': self.date,
                    'line_ids': commands,
                })

        for entry in self.journal_entry_ids:
            if entry.state == 'draft':
                entry.with_company(entry.company_id).action_post()

        self.state = 'posted'
    def reset_to_draft(self):
        self.ensure_one()
        self.state = 'draft'
        for entry in self.journal_entry_ids:
            entry.button_draft()

    def cancel_entries(self):
        self.ensure_one()
        self.state = 'cancel'
        for entry in self.journal_entry_ids:
            entry.button_cancel()

    @api.model
    def _get_default_name(self):
        return self.env["ir.sequence"].next_by_code("account.amount.transfer")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self._get_default_name()
        return super().create(vals_list)

    def unlink(self):
        for transfer in self:
            if transfer.state != 'draft':
                raise UserError(_('You can not delete non draft record.'))
            transfer.journal_entry_ids.unlink()
        return super(AccountAmountTransfer, self).unlink()

    def copy(self, default=None):
        res = super().copy(default)
        res.name = res._get_default_name()
        if res.journal_entry_ids:
            res.journal_entry_ids = False
        return res

    def action_view_journal_entries(self):
        action = self.env["ir.actions.actions"]._for_xml_id("account.action_move_journal_line")
        action['domain'] = [('id', 'in', self.journal_entry_ids.ids)]
        return action

    def _check_company_balance(self):
        self.ensure_one()
        for company in self.transfer_lines.mapped('company_id'):
            lines = self.transfer_lines.filtered(lambda l: l.company_id == company)
            debit = sum(lines.mapped('debit'))
            credit = sum(lines.mapped('credit'))
            if float_compare(debit, credit,
                             precision_digits=company.currency_id.decimal_places) != 0:
                raise UserError(_(
                    "The lines of company %(company)s do not balance: debit "
                    "%(debit)s, credit %(credit)s. The lines of each company "
                    "must balance on their own.",
                    company=company.name, debit=debit, credit=credit))



