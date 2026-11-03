# -*- coding: utf-8 -*-

from odoo import models, fields, api, _


class AccountMove(models.Model):
    _inherit = 'account.move'

    account_amount_transfer_id = fields.Many2one('account.amount.transfer', string='Account Amount Transfer')