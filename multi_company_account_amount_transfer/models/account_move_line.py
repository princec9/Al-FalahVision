# -*- coding: utf-8 -*-

from odoo import models, fields, api, _


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    account_amount_transfer_line_id = fields.Many2one('account.amount.transfer.line', string='Account Amount Transfer Line')