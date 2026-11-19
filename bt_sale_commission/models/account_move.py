# -*- coding: utf-8 -*-
"""
account.move / account.move.line (_inherit)
============================================

Sales Person (Partner) on customer invoices and credit notes, used by the
plans whose Attribution Level is "Invoice" (account.move) or "Invoice Line"
(account.move.line).

The field only applies to ``out_invoice`` / ``out_refund``; on every other
move type it is empty and hidden.

Default value
-------------
* account.move       : the Order Sales Person of the sale order the invoice
                       was created from.
* account.move.line  : the Line Sales Person of the sale order line the
                       invoice line was created from.

Both are independent of each other (like the order header / order line pair)
and both are PLAIN stored fields: filled once when the invoice / line is
created (from the sale order) and never recomputed afterwards, so whatever the
user types on the invoice or on an invoice line is what the commission uses. A credit note made with "Reverse" copies the values of
the invoice it reverses (``copy=True``).
"""
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)
INVOICE_TYPES = ('out_invoice', 'out_refund')


def _refresh_commission_report(env, moves=None):
    if env.context.get('import_file') or env.context.get('skip_commission_refresh'):
        return
    if moves is not None and not any(m.move_type in INVOICE_TYPES for m in moves):
        return
    try:
        with env.cr.savepoint():
            env['sale.commission.partner.report']._ensure_fresh(force=True)
    except Exception:
        _logger.exception("bt_sale_commission: could not refresh the commission "
                          "report after an invoice change.")


class AccountMove(models.Model):
    _inherit = 'account.move'

    # NOT a compute: a stored compute with readonly=False is re-evaluated each
    # time one of its dependencies changes and then overwrote what the user had
    # typed on the invoice.
    salesperson_partner_id = fields.Many2one(
        'res.partner',
        string="Sales Person",
        domain="[('is_salesperson', '=', True)]",
        index=True,
        copy=True,
        check_company=False,
        tracking=True,
        help="Partner (flagged Sales Person) who earns the commission on this "
             "customer invoice / credit note, for plans with Attribution "
             "Level \"Invoice\". Credit notes reduce the amount invoiced.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        moves = super().create(vals_list)
        for move, vals in zip(moves, vals_list):
            # fill from the sale order only when nothing was given
            if (move.move_type in INVOICE_TYPES and not vals.get('salesperson_partner_id')
                    and not move.salesperson_partner_id):
                order = move.invoice_line_ids.sale_line_ids.order_id[:1]
                if order.salesperson_partner_id:
                    move.salesperson_partner_id = order.salesperson_partner_id
        return moves

    @api.onchange('invoice_line_ids')
    def _onchange_bt_salesperson_from_order(self):
        for move in self:
            if move.move_type in INVOICE_TYPES and not move.salesperson_partner_id:
                order = move.invoice_line_ids.sale_line_ids.order_id[:1]
                move.salesperson_partner_id = order.salesperson_partner_id

    # ---- keep Commission by Sales Person in step with the invoices --------
    def write(self, vals):
        res = super().write(vals)
        if {'salesperson_partner_id', 'invoice_date', 'state'} & set(vals):
            _refresh_commission_report(self.env, self)
        return res

    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        _refresh_commission_report(self.env, posted)
        return posted

    def button_draft(self):
        res = super().button_draft()
        _refresh_commission_report(self.env, self)
        return res

    def button_cancel(self):
        res = super().button_cancel()
        _refresh_commission_report(self.env, self)
        return res


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    salesperson_partner_id = fields.Many2one(
        'res.partner',
        string="Sales Person",
        domain="[('is_salesperson', '=', True)]",
        index=True,
        copy=True,
        check_company=False,
        help="Partner (flagged Sales Person) who earns the commission on this "
             "invoice / credit note line, for plans with Attribution Level "
             "\"Invoice Line\". It is independent of the Sales Person on the "
             "invoice header.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        for line, vals in zip(lines, vals_list):
            if (line.display_type == 'product'
                    and line.move_id.move_type in INVOICE_TYPES
                    and not vals.get('salesperson_partner_id')
                    and not line.salesperson_partner_id):
                sp = line.sale_line_ids[:1].salesperson_partner_id
                if sp:
                    line.salesperson_partner_id = sp
        return lines

    def write(self, vals):
        res = super().write(vals)
        if 'salesperson_partner_id' in vals:
            _refresh_commission_report(self.env, self.move_id)
        return res