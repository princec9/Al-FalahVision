# -*- coding: utf-8 -*-
"""
sale.commission.achievement (_inherit)
=======================================

Partner twin of the native manual adjustment ("Add to" / "Reduce From").

Native ``add_user_id`` / ``reduce_user_id`` point at ``sale.commission.plan.user``
(a res.users line of the plan's native Sales People tab). That tab is hidden
by this module, so those fields can never be filled. ``add_partner_id`` /
``reduce_partner_id`` point at ``sale.commission.plan.partner`` (the plan's
partner-based Sales People tab) instead.

An adjustment adds its amount to (or reduces it from) the ACHIEVED amount of
that partner under that plan, in the period its Date falls in -- see
``sale.commission.partner.report._get_period_adjustment``. The native fields
and the native model are left untouched; only the native SCREENS of this
model are changed (user fields hidden, partner fields shown, see _get_view).
"""
import logging

from lxml import etree

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class SaleCommissionAchievement(models.Model):
    _inherit = 'sale.commission.achievement'

    add_partner_id = fields.Many2one(
        'sale.commission.plan.partner', "Add to (Sales Person)", index=True,
        domain=[('plan_id.active', '=', True), ('plan_id.state', '=', 'approved')],
        help="Sales person (on a plan's Sales People tab) whose achieved "
             "amount increases by this adjustment.")
    reduce_partner_id = fields.Many2one(
        'sale.commission.plan.partner', "Reduce From (Sales Person)", index=True,
        domain=[('plan_id.active', '=', True), ('plan_id.state', '=', 'approved')],
        help="Sales person (on a plan's Sales People tab) whose achieved "
             "amount decreases by this adjustment.")

    # Stored shortcuts to the partner behind each line: used to search and
    # group the adjustments list by sales person.
    add_salesperson_id = fields.Many2one(
        'res.partner', string="Added to", related='add_partner_id.partner_id',
        store=True, index=True)
    reduce_salesperson_id = fields.Many2one(
        'res.partner', string="Reduced from", related='reduce_partner_id.partner_id',
        store=True, index=True)

    # ------------------------------------------------------------------
    # Native screens: hide the user-wise fields, show the partner-wise ones.
    #
    # The xml ids of the native views are not known to this module, so rather
    # than inheriting each view by id, every view of this model is adjusted
    # when it is loaded (``_get_view`` is the hook Odoo calls for all of them,
    # whichever module defined the view, and its result is cached).
    #   list / form : the user fields stay in the view but hidden (other
    #                 expressions may still refer to them) and lose any
    #                 "required"; the partner field is added right after.
    #   search / pivot / graph : user fields, filters and group-bys are
    #                 pointed at the partner (sales person) fields.
    # ------------------------------------------------------------------
    _BT_USER_TO_LINE = {
        'add_user_id': 'add_partner_id',
        'reduce_user_id': 'reduce_partner_id',
    }
    _BT_USER_TO_SALESPERSON = {
        'add_user_id': 'add_salesperson_id',
        'reduce_user_id': 'reduce_salesperson_id',
    }

    @api.model
    def _get_view(self, view_id=None, view_type='form', **options):
        arch, view = super()._get_view(view_id, view_type, **options)
        if view_type in ('list', 'form'):
            self._bt_partner_wise_list_form(arch, view_type)
        elif view_type in ('search', 'pivot', 'graph'):
            self._bt_partner_wise_search(arch)
        return arch, view

    @api.model
    def _bt_partner_wise_list_form(self, arch, view_type):
        other = {'add_partner_id': 'reduce_partner_id',
                 'reduce_partner_id': 'add_partner_id'}
        for node in arch.xpath(
                "//field[@name='add_user_id' or @name='reduce_user_id']"):
            line_field = self._BT_USER_TO_LINE[node.get('name')]
            if arch.xpath("//field[@name='%s']" % line_field):
                continue  # partner field already in this view
            # hide the user field (keep it: other expressions may use it)
            node.attrib.pop('required', None)
            node.set('column_invisible' if view_type == 'list' else 'invisible', '1')
            # partner field in its place
            new = etree.Element('field', name=line_field)
            new.set('options', "{'no_create': True, 'no_open': True}")
            new.set('required', "not %s" % other[line_field])
            if node.get('optional'):
                new.set('optional', node.get('optional'))
            node.addnext(new)

    @api.model
    def _bt_partner_wise_search(self, arch):
        for element in arch.iter():
            for key, value in list(element.attrib.items()):
                new_value = value
                for user_field, salesperson_field in self._BT_USER_TO_SALESPERSON.items():
                    new_value = new_value.replace(user_field, salesperson_field)
                if new_value != value:
                    element.set(key, new_value)

    # ------------------------------------------------------------------
    # Keep Commission by Sales Person in step with the adjustments. That
    # report is a table rebuilt on demand, so without this an adjustment
    # would only show up after the next scheduled rebuild (15 min) or after
    # opening the "Commission by Sales Person" menu.
    # ------------------------------------------------------------------
    _BT_REPORT_FIELDS = {'add_partner_id', 'reduce_partner_id', 'achieved',
                         'date', 'currency_id', 'company_id'}

    def _bt_refresh_partner_report(self):
        try:
            with self.env.cr.savepoint():
                self.env['sale.commission.partner.report']._ensure_fresh(force=True)
        except Exception:
            # saving an adjustment must never fail because of the report
            _logger.exception(
                "bt_sale_commission: could not refresh Commission by Sales "
                "Person after an adjustment change.")

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        if any(v.get('add_partner_id') or v.get('reduce_partner_id') for v in vals_list):
            records._bt_refresh_partner_report()
        return records

    def write(self, vals):
        res = super().write(vals)
        if self._BT_REPORT_FIELDS & set(vals):
            self._bt_refresh_partner_report()
        return res

    def unlink(self):
        touches_partner = any(r.add_partner_id or r.reduce_partner_id for r in self)
        env = self.env
        res = super().unlink()
        if touches_partner:
            env['sale.commission.achievement']._bt_refresh_partner_report()
        return res

    @api.constrains('add_partner_id', 'reduce_partner_id')
    def _check_partner_lines_differ(self):
        for adjustment in self:
            if (adjustment.add_partner_id
                    and adjustment.add_partner_id == adjustment.reduce_partner_id):
                raise ValidationError(_(
                    "An adjustment cannot add to and reduce from the same "
                    "sales person of the same plan."))
